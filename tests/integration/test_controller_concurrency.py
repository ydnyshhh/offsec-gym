"""M6.0: competing controller instances must agree on run-scoped decisions."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import CoverageClaim, EntityRef, WorldFact
from offsecgym.schemas.events import (
    ActionReservationAcquired,
    ActionReservationReleased,
    CoverageLeaseAcquired,
    ModelBudgetReserved,
    ModelBudgetSettled,
    ModelCallStarted,
    WorkerFinished,
    WorkerSpawned,
    WorkerStarted,
)
from offsecgym.schemas.specs import Budget
from offsecgym.storage.controller import PostgresControllerState
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events
from offsecgym.worldview import EventWorldState


@pytest.fixture
async def controllers():
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    first_engine = create_async_engine(url)
    second_engine = create_async_engine(url)
    first_store = PostgresEventStore(first_engine)
    second_store = PostgresEventStore(second_engine)
    try:
        yield (
            PostgresControllerState(first_store),
            PostgresControllerState(second_store),
            first_store,
            second_store,
        )
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


@pytest.mark.postgres
async def test_same_request_has_exactly_one_active_owner(controllers) -> None:
    first, second, events, _ = controllers
    run_id = uuid4()
    task_id = uuid4()
    actions = [
        ActionRequest(
            run_id=run_id,
            worker_id=uuid4(),
            task_id=task_id,
            kind="http_request",
            destination="saas",
            method="POST",
            path="/api/invoices/x",
            json_body={"a": 1, "b": 2},
        )
        for _ in range(20)
    ]
    actions[1] = actions[1].model_copy(update={"json_body": {"b": 2, "a": 1}})
    outcomes = await asyncio.gather(
        *(
            controller.reserve_action(action, Budget(max_actions=20))
            for controller, action in zip((first, second) * 10, actions, strict=True)
        )
    )
    assert outcomes.count(None) == 1
    assert outcomes.count("action_already_reserved") == 19
    assert (await first.snapshot(run_id))["used_actions"] == 1
    winner = actions[outcomes.index(None)]
    with pytest.raises(ValueError, match="absent or already released"):
        await second.release_action(winner.model_copy(update={"worker_id": uuid4()}))
    await second.release_action(winner)
    assert (
        await first.reserve_action(
            actions[1 if winner != actions[1] else 0], Budget(max_actions=20)
        )
        is None
    )
    trace = await events.read_run(run_id)
    assert len([event for event in trace if isinstance(event, ActionReservationAcquired)]) == 2
    assert len([event for event in trace if isinstance(event, ActionReservationReleased)]) == 1


@pytest.mark.postgres
async def test_distinct_actions_never_oversubscribe_budget(controllers) -> None:
    first, second, events, _ = controllers
    run_id = uuid4()
    actions = [
        ActionRequest(
            run_id=run_id,
            worker_id=uuid4(),
            task_id=uuid4(),
            kind="http_request",
            destination="saas",
            method="GET",
            path=f"/api/documents/{number}",
        )
        for number in range(24)
    ]
    results = await asyncio.gather(
        *(
            controller.reserve_action(action, Budget(max_actions=5, max_http_requests=5))
            for controller, action in zip((first, second) * 12, actions, strict=True)
        )
    )
    assert results.count(None) == 5
    assert set(results) == {None, "action_budget_exhausted"}
    usage = await first.snapshot(run_id)
    assert usage["used_actions"] == usage["used_http_requests"] == 5
    trace = await events.read_run(run_id)
    assert len([event for event in trace if isinstance(event, ActionReservationAcquired)]) == 5
    projected = project_controller_events(trace)
    assert projected.used_actions == usage["used_actions"]
    assert projected.used_http_requests == usage["used_http_requests"]
    assert projected.last_dispatch_at == usage["last_dispatch_at"]
    assert projected.budget == Budget(max_actions=5, max_http_requests=5)
    with pytest.raises(ValueError, match="declared global budget"):
        await second.reserve_action(actions[6], Budget(max_actions=6, max_http_requests=5))
    assert (await first.snapshot(run_id))["used_actions"] == 5


@pytest.mark.postgres
async def test_model_start_and_budget_reservation_commit_together(controllers) -> None:
    first, _, events, _ = controllers
    run_id, call_id = uuid4(), uuid4()
    started = ModelCallStarted(
        run_id=run_id,
        actor="controller",
        call_id=call_id,
        provider="mock",
        model="mock-model",
        input_sha256="a" * 64,
        request_artifact_id=uuid4(),
        request_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="does not match reservation"):
        await first.reserve_model_call(
            run_id,
            call_id,
            Budget(max_model_calls=1),
            estimated_tokens=5,
            started_event=started.model_copy(update={"call_id": uuid4()}),
        )
    assert (await first.snapshot(run_id))["used_model_calls"] == 0
    assert (
        await first.reserve_model_call(
            run_id,
            call_id,
            Budget(max_model_calls=1),
            estimated_tokens=5,
            started_event=started,
        )
        is None
    )
    trace = await events.read_run(run_id)
    assert [event.type for event in trace] == [
        "controller_budget_declared",
        "model_budget_reserved",
        "model_call_started",
    ]


@pytest.mark.postgres
async def test_coverage_and_world_facts_are_atomic_across_instances(controllers) -> None:
    _, _, first_store, second_store = controllers
    first, second = EventWorldState(first_store), EventWorldState(second_store)
    run_id = uuid4()
    claims = [
        CoverageClaim(
            claim_id=uuid4(),
            run_id=run_id,
            task_id=uuid4(),
            component="Billing" if number else "billing",
            objective="test invoice access",
        )
        for number in range(2)
    ]
    outcomes = await asyncio.gather(
        first.claim_coverage(claims[0]),
        second.claim_coverage(claims[1]),
        return_exceptions=True,
    )
    assert sum(isinstance(item, CoverageClaim) for item in outcomes) == 1
    assert sum(isinstance(item, ValueError) for item in outcomes) == 1
    claim = next(item for item in outcomes if isinstance(item, CoverageClaim))
    trace = await first_store.read_run(run_id)
    assert len([event for event in trace if isinstance(event, CoverageLeaseAcquired)]) == 1
    fact = WorldFact(
        fact_id=uuid4(),
        run_id=run_id,
        kind="hypothesis",
        subject=EntityRef(entity_id=uuid4(), entity_type="endpoint"),
        predicate="route",
        object_value="GET /api/invoices/{id}",
        source_event_ids=(trace[0].event_id,),
        confidence=0.2,
    )
    fact_outcomes = await asyncio.gather(
        first.submit_fact(fact), second.submit_fact(fact), return_exceptions=True
    )
    assert sum(isinstance(item, WorldFact) for item in fact_outcomes) == 1
    assert sum(isinstance(item, ValueError) for item in fact_outcomes) == 1
    await second.update_coverage(run_id, claim.claim_id, claim.task_id, "completed")
    assert (await first.coverage(run_id))[0].status == "completed"


@pytest.mark.postgres
async def test_worker_slots_and_model_reservations_remain_bounded(controllers) -> None:
    first, second, events, _ = controllers
    run_id = uuid4()
    budget = Budget(
        max_workers=2,
        max_concurrency=1,
        max_model_calls=2,
        max_total_tokens=100,
        max_cost_usd=0.0001,
    )
    workers = [(uuid4(), uuid4()) for _ in range(3)]
    spawned = await asyncio.gather(
        *(
            controller.spawn_worker(run_id, worker, task, "invoice testing", budget)
            for controller, (worker, task) in zip((first, second, first), workers, strict=True)
        )
    )
    assert spawned.count(None) == 2
    assert spawned.count("worker_budget_exhausted") == 1
    active = [
        (worker, task)
        for (worker, task), result in zip(workers, spawned, strict=True)
        if result is None
    ]
    started = await asyncio.gather(
        first.start_worker(run_id, *active[0], budget),
        second.start_worker(run_id, *active[1], budget),
    )
    assert set(started) == {None, "worker_slots_exhausted"}
    winner = active[started.index(None)]
    loser = active[started.index("worker_slots_exhausted")]
    await second.finish_worker(run_id, *winner, "completed")
    assert await first.start_worker(run_id, *loser, budget) is None
    calls = [uuid4() for _ in range(8)]
    reserved = await asyncio.gather(
        *(
            controller.reserve_model_call(
                run_id,
                call_id,
                budget,
                estimated_tokens=40,
                estimated_cost_microusd=40,
                worker_id=winner[0],
                task_id=winner[1],
            )
            for controller, call_id in zip((first, second) * 4, calls, strict=True)
        )
    )
    assert reserved.count(None) == 2
    assert reserved.count("model_call_budget_exhausted") == 6
    usage = await first.snapshot(run_id)
    assert usage["active_workers"] == 1
    assert usage["used_model_calls"] == 2
    assert usage["reserved_tokens"] == 80
    assert usage["reserved_cost_microusd"] == 80
    for call_id, outcome in zip(calls, reserved, strict=True):
        if outcome is None:
            await second.settle_model_call(
                run_id,
                call_id,
                actual_tokens=30,
                actual_cost_microusd=30,
                worker_id=winner[0],
                task_id=winner[1],
            )
    usage = await first.snapshot(run_id)
    assert usage["reserved_tokens"] == usage["reserved_cost_microusd"] == 0
    assert usage["used_tokens"] == usage["used_cost_microusd"] == 60
    trace = await events.read_run(run_id)
    projected = project_controller_events(trace)
    for field in (
        "used_actions",
        "used_http_requests",
        "used_model_calls",
        "used_tokens",
        "reserved_tokens",
        "used_cost_microusd",
        "reserved_cost_microusd",
        "spawned_workers",
        "active_workers",
        "last_dispatch_at",
    ):
        assert getattr(projected, field) == usage[field]
    assert len([item for item in trace if isinstance(item, WorkerSpawned)]) == 2
    assert len([item for item in trace if isinstance(item, WorkerStarted)]) == 2
    assert len([item for item in trace if isinstance(item, WorkerFinished)]) == 1
    assert len([item for item in trace if isinstance(item, ModelBudgetReserved)]) == 2
    assert len([item for item in trace if isinstance(item, ModelBudgetSettled)]) == 2
    assert all(
        item.worker_id == winner[0] and item.task_id == winner[1]
        for item in trace
        if isinstance(item, (ModelBudgetReserved, ModelBudgetSettled))
    )
