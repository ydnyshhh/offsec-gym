"""M6.0: competing controller instances must agree on run-scoped decisions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.providers.base import ModelTurn
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import (
    AgentContext,
    AgentTask,
    AgentVisibleRangeContext,
    CoverageClaim,
    EntityRef,
    WorldFact,
)
from offsecgym.schemas.events import (
    ActionAttemptReserved,
    ActionReservationAcquired,
    ActionReservationReleased,
    CoverageLeaseAcquired,
    ModelBudgetReserved,
    ModelBudgetSettled,
    ModelCallCompleted,
    ModelCallStarted,
    ModelReservationRejected,
    ModelToolRejected,
    WorkerBlocked,
    WorkerBudgetEscrowDeclared,
    WorkerContractViolated,
    WorkerDebriefed,
    WorkerFinished,
    WorkerHeartbeat,
    WorkerLeaseRecovered,
    WorkerOriented,
    WorkerPacketPrepared,
    WorkerSpawned,
    WorkerStarted,
)
from offsecgym.schemas.specs import Budget, ModelSpec
from offsecgym.solver.matched_workers import MatchedWorkerCoordinator
from offsecgym.solver.monolithic import MonolithicSaasAgent
from offsecgym.solver.workers import SequentialWorkerCoordinator
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
            controller.reserve_action(action, Budget(max_actions=21))
            for controller, action in zip((first, second) * 10, actions, strict=True)
        )
    )
    assert outcomes.count(None) == 1
    assert outcomes.count("action_already_reserved") == 19
    assert (await first.snapshot(run_id))["used_actions"] == 20
    assert (await first.snapshot(run_id))["used_http_requests"] == 1
    winner = actions[outcomes.index(None)]
    with pytest.raises(ValueError, match="absent or already released"):
        await second.release_action(winner.model_copy(update={"worker_id": uuid4()}))
    await second.release_action(winner)
    later = winner.model_copy(update={"action_id": uuid4()})
    assert await first.reserve_action(later, Budget(max_actions=21)) is None
    trace = await events.read_run(run_id)
    assert len([event for event in trace if isinstance(event, ActionReservationAcquired)]) == 2
    assert len([event for event in trace if isinstance(event, ActionReservationReleased)]) == 1
    assert len([event for event in trace if isinstance(event, ActionAttemptReserved)]) == 21


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
            reserved_input_tokens=3,
            reserved_output_tokens=2,
            request_bytes=6,
            started_event=started.model_copy(update={"call_id": uuid4()}),
        )
    assert (await first.snapshot(run_id))["used_model_calls"] == 0
    assert (
        await first.reserve_model_call(
            run_id,
            call_id,
            Budget(max_model_calls=1),
            reserved_input_tokens=3,
            reserved_output_tokens=2,
            request_bytes=6,
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
async def test_model_agent_uses_shared_budget_and_worker_attribution(controllers, tmp_path) -> None:
    _, _, events, _ = controllers
    run_id, worker_id, task_id = uuid4(), uuid4(), uuid4()
    budget = Budget(
        max_actions=2, max_model_calls=2, max_total_tokens=20000, max_output_tokens_per_call=128
    )

    class OneTurnProvider:
        def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
            return {
                "model": model.name,
                "instructions": instructions,
                "input": input_items,
                "max_output_tokens": max_output_tokens,
            }

        async def complete(self, request_payload):
            return ModelTurn(
                response_id="synthetic",
                status="completed",
                output=(),
                usage={"input_tokens": 20, "output_tokens": 10},
                raw_response={
                    "id": "synthetic",
                    "status": "completed",
                    "output": [],
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                },
            )

    agent = MonolithicSaasAgent(
        OneTurnProvider(),
        ModelSpec(provider="openai", name="synthetic"),
        None,
        events,
        tmp_path,
    )
    task = AgentTask(task_id=task_id, worker_id=worker_id, goal="Inspect billing", budget=budget)
    context = AgentContext(
        run_id=run_id,
        objective=task.goal,
        global_budget=budget,
        range=AgentVisibleRangeContext(
            range_instance_id=uuid4(), range_generation=0, family="saas"
        ),
    )
    result = await agent.run(task, context, None)
    assert result.status == "completed"
    trace = await events.read_run(run_id)
    model_events = [
        item
        for item in trace
        if isinstance(
            item, (ModelBudgetReserved, ModelCallStarted, ModelBudgetSettled, ModelCallCompleted)
        )
    ]
    assert len(model_events) == 4
    assert all(item.worker_id == worker_id and item.task_id == task_id for item in model_events)
    usage = await PostgresControllerState(events).snapshot(run_id)
    assert usage["used_model_calls"] == 1
    assert usage["used_tokens"] == 30 and usage["reserved_tokens"] == 0


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
                reserved_input_tokens=30,
                reserved_output_tokens=10,
                request_bytes=75,
                estimated_cost_microusd=40,
                worker_id=loser[0],
                task_id=loser[1],
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
                actual_input_tokens=22,
                actual_output_tokens=8,
                actual_cost_microusd=30,
                worker_id=loser[0],
                task_id=loser[1],
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
    rejections = [item for item in trace if isinstance(item, ModelReservationRejected)]
    assert len(rejections) == 6
    assert all(item.reason_code == "model_call_budget_exhausted" for item in rejections)
    assert {item.call_id for item in rejections} == {
        call_id for call_id, outcome in zip(calls, reserved, strict=True) if outcome is not None
    }
    assert len([item for item in trace if isinstance(item, ModelBudgetSettled)]) == 2
    for item in trace:
        if isinstance(item, ModelBudgetReserved):
            assert (item.reserved_input_tokens, item.reserved_output_tokens) == (30, 10)
            assert item.request_bytes == 75
        elif isinstance(item, ModelBudgetSettled):
            assert (item.actual_input_tokens, item.actual_output_tokens) == (22, 8)
            assert item.reservation_error == -10
            assert item.input_reservation_error == -8
    assert all(
        item.worker_id == loser[0] and item.task_id == loser[1]
        for item in trace
        if isinstance(item, (ModelBudgetReserved, ModelBudgetSettled))
    )


@pytest.mark.postgres
async def test_hard_worker_escrows_are_atomic_and_replayable(controllers) -> None:
    first, second, events, _ = controllers
    run_id = uuid4()
    worker_budget = Budget(
        max_model_calls=4,
        max_total_tokens=200,
        max_actions=4,
        max_http_requests=4,
        max_workers=2,
        max_concurrency=2,
    )
    workers = [(uuid4(), uuid4()) for _ in range(2)]
    slices = [
        Budget(max_model_calls=2, max_total_tokens=100, max_actions=2, max_http_requests=2)
        for _ in workers
    ]
    await first.declare_worker_escrows(
        run_id,
        [
            (worker_id, task_id, "invoice testing", slice_budget)
            for (worker_id, task_id), slice_budget in zip(workers, slices, strict=True)
        ],
        worker_budget,
        worker_budget,
    )
    with pytest.raises(ValueError, match="declared once"):
        await second.declare_worker_escrows(
            run_id,
            [(workers[0][0], workers[0][1], "invoice testing", slices[0])],
            worker_budget,
            worker_budget,
        )
    for worker_id, task_id in workers:
        assert (
            await first.spawn_worker(run_id, worker_id, task_id, "invoice", worker_budget) is None
        )
        assert await second.start_worker(run_id, worker_id, task_id, worker_budget) is None

    calls = [uuid4() for _ in range(3)]
    results = await asyncio.gather(
        first.reserve_model_call(
            run_id,
            calls[0],
            worker_budget,
            reserved_input_tokens=60,
            reserved_output_tokens=20,
            request_bytes=100,
            worker_id=workers[0][0],
            task_id=workers[0][1],
        ),
        second.reserve_model_call(
            run_id,
            calls[1],
            worker_budget,
            reserved_input_tokens=60,
            reserved_output_tokens=20,
            request_bytes=100,
            worker_id=workers[0][0],
            task_id=workers[0][1],
        ),
    )
    assert results.count(None) == 1
    assert results.count("worker_token_escrow_exhausted") == 1
    assert (
        await second.reserve_model_call(
            run_id,
            calls[2],
            worker_budget,
            reserved_input_tokens=60,
            reserved_output_tokens=20,
            request_bytes=100,
            worker_id=workers[1][0],
            task_id=workers[1][1],
        )
        is None
    )
    accounts = await first.worker_escrow_snapshot(run_id)
    assert len(accounts) == 2
    assert {row["reserved_tokens"] for row in accounts} == {80}
    assert {row["used_model_calls"] for row in accounts} == {1}
    for call_id, result in zip(calls[:2], results, strict=True):
        if result is None:
            await second.settle_model_call(
                run_id,
                call_id,
                actual_input_tokens=50,
                actual_output_tokens=10,
                worker_id=workers[0][0],
                task_id=workers[0][1],
            )
    await first.settle_model_call(
        run_id,
        calls[2],
        actual_input_tokens=50,
        actual_output_tokens=10,
        worker_id=workers[1][0],
        task_id=workers[1][1],
    )
    accounts = await first.worker_escrow_snapshot(run_id)
    assert {row["used_tokens"] for row in accounts} == {60}
    assert {row["reserved_tokens"] for row in accounts} == {0}

    actions = [
        ActionRequest(
            run_id=run_id,
            worker_id=workers[0][0],
            task_id=workers[0][1],
            kind="http_request",
            destination="saas",
            method="GET",
            path=f"/api/invoices/{index}",
        )
        for index in range(3)
    ]
    outcomes = await asyncio.gather(
        *(
            controller.reserve_action(action, worker_budget)
            for controller, action in zip((first, second), actions[:2], strict=True)
        )
    )
    assert outcomes == [None, None]
    assert await first.reserve_action(actions[2], worker_budget) == "worker_action_escrow_exhausted"
    for action in actions[:2]:
        await first.release_action(action)
    trace = await events.read_run(run_id)
    projection = project_controller_events(trace)
    assert len([event for event in trace if isinstance(event, WorkerBudgetEscrowDeclared)]) == 2
    for account in accounts:
        replay = projection.worker_escrows[account["worker_id"]]
        assert replay.used_model_calls == account["used_model_calls"]
        assert replay.used_tokens == account["used_tokens"]
    assert projection.worker_escrows[workers[0][0]].used_actions == 2
    assert not projection.active_actions and not projection.model_reservations


@pytest.mark.postgres
async def test_stale_worker_recovery_settles_its_escrow(controllers) -> None:
    first, _, events, _ = controllers
    run_id, worker_id, task_id, call_id = (uuid4() for _ in range(4))
    budget = Budget(
        max_model_calls=2,
        max_total_tokens=100,
        max_actions=1,
        max_http_requests=1,
        max_workers=1,
        max_concurrency=1,
    )
    await first.declare_worker_escrows(
        run_id, [(worker_id, task_id, "invoice", budget)], budget, budget
    )
    assert await first.spawn_worker(run_id, worker_id, task_id, "invoice", budget) is None
    assert await first.start_worker(run_id, worker_id, task_id, budget) is None
    assert (
        await first.reserve_model_call(
            run_id,
            call_id,
            budget,
            reserved_input_tokens=60,
            reserved_output_tokens=20,
            request_bytes=100,
            worker_id=worker_id,
            task_id=task_id,
        )
        is None
    )
    trace = await events.read_run(run_id)
    start = next(item for item in trace if isinstance(item, WorkerStarted))
    assert start.lease_expires_at is not None
    assert (
        await first.reconcile_stale_workers(
            run_id, now=start.lease_expires_at + timedelta(seconds=1)
        )
        == 1
    )
    account = (await first.worker_escrow_snapshot(run_id))[0]
    assert account["used_model_calls"] == 1
    assert account["reserved_tokens"] == account["used_tokens"] == 0
    projection = project_controller_events(await events.read_run(run_id))
    replay = projection.worker_escrows[worker_id]
    assert replay.used_model_calls == 1
    assert replay.reserved_tokens == replay.used_tokens == 0
    assert not projection.model_reservations and projection.active_workers == 0


@pytest.mark.postgres
async def test_sequential_workers_have_bounded_packets_and_attributed_calls(
    controllers, tmp_path
) -> None:
    _, _, events, _ = controllers
    run_id = uuid4()
    budget = Budget(
        max_actions=6,
        max_http_requests=6,
        max_model_calls=6,
        max_total_tokens=120000,
        max_output_tokens_per_call=128,
        max_workers=6,
        max_concurrency=1,
    )

    class OneTurnProvider:
        def __init__(self):
            self.requests = []

        def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
            payload = {
                "model": model.name,
                "instructions": instructions,
                "input": input_items,
                "max_output_tokens": max_output_tokens,
            }
            self.requests.append(payload)
            return payload

        async def complete(self, request_payload):
            return ModelTurn(
                response_id="synthetic",
                status="completed",
                output=(),
                usage={"input_tokens": 200, "output_tokens": 10},
                raw_response={
                    "id": "synthetic",
                    "status": "completed",
                    "output": [],
                    "usage": {"input_tokens": 200, "output_tokens": 10},
                },
            )

    provider = OneTurnProvider()
    coordinator = SequentialWorkerCoordinator(
        provider, ModelSpec(provider="openai", name="synthetic"), None, events, tmp_path
    )
    task = AgentTask(task_id=uuid4(), goal="Test SaaS security", budget=budget)
    context = AgentContext(
        run_id=run_id,
        objective=task.goal,
        global_budget=budget,
        range=AgentVisibleRangeContext(range_instance_id=uuid4(), family="saas"),
    )
    result = await coordinator.run(task, context, None)
    assert result.status == "failed"
    assert len(provider.requests) == 6
    trace = await events.read_run(run_id)
    packets = [item for item in trace if isinstance(item, WorkerPacketPrepared)]
    debriefs = [item for item in trace if isinstance(item, WorkerDebriefed)]
    assert len(packets) == len(debriefs) == 6
    assert len([item for item in trace if isinstance(item, WorkerContractViolated)]) == 6
    assert len({item.worker_id for item in packets}) == 6
    assert all(len(item.packet.model_dump_json()) <= 10000 for item in packets)
    assert all(item.packet.budget_slice.max_model_calls == 1 for item in packets)
    starts = [item for item in trace if isinstance(item, ModelCallStarted)]
    assert len(starts) == 6
    assert {(item.worker_id, item.task_id) for item in starts} == {
        (item.worker_id, item.task_id) for item in packets
    }
    assert all("Worker packet:" in item["input"][0]["content"] for item in provider.requests)
    assert all(
        item["input"][1]["content"].startswith("Worker-local updates since the handoff packet:")
        and "Relevant world facts" not in item["input"][1]["content"]
        and len(item["input"][1]["content"]) <= 2600
        for item in provider.requests
    )
    projected = project_controller_events(trace)
    assert projected.used_model_calls == projected.spawned_workers == 6
    assert projected.active_workers == 0
    assert set(projected.worker_status.values()) == {"finished"}
    assert len(projected.worker_packets) == len(projected.worker_debriefs) == 6
    metrics = orchestration_metrics(trace)
    assert metrics.worker_packets == metrics.worker_debriefs == 6
    assert metrics.coordinator_model_calls == 0
    coverage = await EventWorldState(events).coverage(run_id)
    assert len(coverage) == 6
    assert all(item.status == "released" for item in coverage)


@pytest.mark.postgres
async def test_worker_retrieval_turn_requires_action_or_typed_block(controllers, tmp_path) -> None:
    _, _, events, _ = controllers
    run_id = uuid4()
    budget = Budget(
        max_actions=6,
        max_http_requests=6,
        max_model_calls=12,
        max_total_tokens=240000,
        max_output_tokens_per_call=128,
        max_workers=6,
        max_concurrency=1,
    )

    class OrientThenBlockProvider:
        def __init__(self):
            self.turns = {}
            self.tool_names = []

        def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
            return {
                "model": model.name,
                "instructions": instructions,
                "input": input_items,
                "tools": tools,
                "max_output_tokens": max_output_tokens,
            }

        async def complete(self, request_payload):
            objective = re.search(r"Goal: ([^.]+)\.", request_payload["input"][0]["content"])
            assert objective is not None
            key = objective.group(1)
            step = self.turns.get(key, 0)
            self.turns[key] = step + 1
            names = {item["name"] for item in request_payload["tools"]}
            self.tool_names.append(names)
            if step == 0:
                assert "query_worldview" in names
                name, args = "query_worldview", {"query": key, "kind": None}
            else:
                assert names == {"http_request", "task_blocked"}
                name, args = (
                    "task_blocked",
                    {
                        "reason": "No action is defined for this fake provider",
                        "missing_prerequisite": "A configured synthetic action policy",
                    },
                )
            output = (
                {
                    "type": "function_call",
                    "call_id": f"{key}:{step}",
                    "name": name,
                    "arguments": json.dumps(args),
                },
            )
            raw = {
                "id": f"{key}:{step}",
                "status": "completed",
                "output": list(output),
                "usage": {"input_tokens": 200, "output_tokens": 10},
            }
            return ModelTurn(
                response_id=raw["id"],
                status="completed",
                output=output,
                usage=raw["usage"],
                raw_response=raw,
            )

    provider = OrientThenBlockProvider()
    coordinator = SequentialWorkerCoordinator(
        provider, ModelSpec(provider="openai", name="synthetic"), None, events, tmp_path
    )
    task = AgentTask(task_id=uuid4(), goal="Test SaaS security", budget=budget)
    context = AgentContext(
        run_id=run_id,
        objective=task.goal,
        global_budget=budget,
        range=AgentVisibleRangeContext(range_instance_id=uuid4(), family="saas"),
    )
    result = await coordinator.run(task, context, None)
    assert result.status == "completed"
    assert len(provider.tool_names) == 12
    trace = await events.read_run(run_id)
    assert len([item for item in trace if isinstance(item, WorkerOriented)]) == 6
    assert len([item for item in trace if isinstance(item, WorkerBlocked)]) == 6
    assert not any(isinstance(item, WorkerContractViolated) for item in trace)
    projected = project_controller_events(trace)
    assert set(projected.worker_outcomes.values()) == {"blocked"}


@pytest.mark.postgres
async def test_worker_tool_rejections_have_typed_attributed_reasons(controllers, tmp_path) -> None:
    _, _, events, _ = controllers
    run_id = uuid4()
    budget = Budget(
        max_actions=6,
        max_http_requests=6,
        max_model_calls=12,
        max_total_tokens=240000,
        max_output_tokens_per_call=128,
        max_workers=6,
        max_concurrency=1,
    )

    class InvalidThenBlockProvider:
        def __init__(self):
            self.turns = {}

        def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
            return {"input": input_items}

        async def complete(self, request_payload):
            match = re.search(r"Goal: ([^.]+)\.", request_payload["input"][0]["content"])
            assert match is not None
            key = match.group(1)
            step = self.turns.get(key, 0)
            self.turns[key] = step + 1
            if step == 0:
                proposals = [("query_worldview", {"query": key, "kind": None})]
            else:
                proposals = [
                    ("query_worldview", {"query": key, "kind": None}),
                    (
                        "http_request",
                        {
                            "method": "POST" if "refund" in key else "GET",
                            "path": "/api/unrelated",
                            "identity_id": None,
                            "body_json": None,
                        },
                    ),
                    (
                        "task_blocked",
                        {"reason": "Synthetic invalid proposals", "missing_prerequisite": "Target"},
                    ),
                ]
            output = tuple(
                {
                    "type": "function_call",
                    "call_id": f"{key}:{step}:{index}",
                    "name": name,
                    "arguments": json.dumps(args),
                }
                for index, (name, args) in enumerate(proposals)
            )
            raw = {
                "id": f"{key}:{step}",
                "status": "completed",
                "output": list(output),
                "usage": {"input_tokens": 200, "output_tokens": 10},
            }
            return ModelTurn(
                response_id=raw["id"],
                status="completed",
                output=output,
                usage=raw["usage"],
                raw_response=raw,
            )

    coordinator = SequentialWorkerCoordinator(
        InvalidThenBlockProvider(),
        ModelSpec(provider="openai", name="synthetic"),
        None,
        events,
        tmp_path,
    )
    task = AgentTask(task_id=uuid4(), goal="Test SaaS security", budget=budget)
    context = AgentContext(
        run_id=run_id,
        objective=task.goal,
        global_budget=budget,
        range=AgentVisibleRangeContext(range_instance_id=uuid4(), family="saas"),
    )
    await coordinator.run(task, context, None)
    rejected = [
        event for event in await events.read_run(run_id) if isinstance(event, ModelToolRejected)
    ]
    assert len(rejected) == 12
    assert {event.reason_code for event in rejected} == {
        "worker_action_required",
        "worker_route_mismatch",
    }
    assert all(event.worker_id is not None and event.task_id is not None for event in rejected)
    http = [event for event in rejected if event.tool_name == "http_request"]
    assert len(http) == 6
    assert all(
        event.proposed_path_sha256 == hashlib.sha256(b"/api/unrelated").hexdigest()
        for event in http
    )
    assert {event.proposed_method for event in http} == {"GET", "POST"}


@pytest.mark.postgres
async def test_matched_packets_vary_only_worker_scheduling(controllers, tmp_path) -> None:
    _, _, events, _ = controllers

    class SlowOneTurnProvider:
        def __init__(self):
            self.active = 0
            self.peak = 0

        def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
            return {"input": input_items, "max_output_tokens": max_output_tokens}

        async def complete(self, request_payload):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.03)
                raw = {
                    "id": "synthetic",
                    "status": "completed",
                    "output": [],
                    "usage": {"input_tokens": 200, "output_tokens": 10},
                }
                return ModelTurn(
                    response_id="synthetic",
                    status="completed",
                    output=(),
                    usage=raw["usage"],
                    raw_response=raw,
                )
            finally:
                self.active -= 1

    packets_by_arm = []
    for parallel in (False, True):
        run_id = uuid4()
        budget = Budget(
            max_actions=60,
            max_http_requests=60,
            max_model_calls=20,
            max_total_tokens=120000,
            max_output_tokens_per_call=8192,
            max_workers=6,
            max_concurrency=6 if parallel else 1,
        )
        provider = SlowOneTurnProvider()
        coordinator = MatchedWorkerCoordinator(
            provider,
            ModelSpec(provider="openai", name="synthetic"),
            None,
            events,
            tmp_path,
            parallel=parallel,
        )
        task = AgentTask(task_id=uuid4(), goal="Test SaaS security", budget=budget)
        context = AgentContext(
            run_id=run_id,
            objective=task.goal,
            global_budget=budget,
            range=AgentVisibleRangeContext(range_instance_id=uuid4(), family="saas"),
        )
        result = await coordinator.run(task, context, None)
        assert result.status == "failed"
        assert provider.peak >= (2 if parallel else 1)
        if not parallel:
            assert provider.peak == 1
        trace = await events.read_run(run_id)
        assert project_controller_events(trace).active_workers == 0
        metrics = orchestration_metrics(trace)
        if parallel:
            assert metrics.worker_overlap_pairs > 0
        else:
            assert metrics.worker_overlap_pairs == 0
        assert metrics.mean_worker_queue_wait_seconds is not None
        assert len([event for event in trace if isinstance(event, WorkerContractViolated)]) == 6
        packets = [event.packet for event in trace if isinstance(event, WorkerPacketPrepared)]
        packets_by_arm.append(
            sorted(
                [
                    (
                        packet.objective,
                        packet.contract,
                        packet.budget_slice,
                        packet.relevant_entities,
                        packet.relevant_evidence,
                        packet.prior_checked_actions,
                    )
                    for packet in packets
                ],
                key=lambda item: item[0],
            )
        )
    assert packets_by_arm[0] == packets_by_arm[1]


@pytest.mark.postgres
async def test_stale_worker_recovery_releases_all_owned_reservations(controllers) -> None:
    first, second, events, _ = controllers
    run_id, worker_id, task_id = uuid4(), uuid4(), uuid4()
    budget = Budget(
        max_workers=2,
        max_concurrency=1,
        max_actions=2,
        max_http_requests=2,
        max_model_calls=2,
        max_total_tokens=1000,
    )
    assert await first.spawn_worker(run_id, worker_id, task_id, "invoice", budget) is None
    assert await first.start_worker(run_id, worker_id, task_id, budget) is None
    world = EventWorldState(events)
    claim = CoverageClaim(
        claim_id=uuid4(),
        run_id=run_id,
        task_id=task_id,
        component="invoice",
        objective="object authorization",
    )
    await world.claim_coverage(claim)
    action = ActionRequest(
        run_id=run_id,
        worker_id=worker_id,
        task_id=task_id,
        kind="http_request",
        destination="saas",
        method="GET",
        path="/api/invoices/example",
    )
    assert await first.reserve_action(action, budget) is None
    call_id = uuid4()
    started = ModelCallStarted(
        run_id=run_id,
        actor="controller",
        call_id=call_id,
        worker_id=worker_id,
        task_id=task_id,
        provider="mock",
        model="mock",
        input_sha256="a" * 64,
        request_artifact_id=uuid4(),
        request_sha256="a" * 64,
    )
    assert (
        await first.reserve_model_call(
            run_id,
            call_id,
            budget,
            reserved_input_tokens=30,
            reserved_output_tokens=10,
            request_bytes=80,
            worker_id=worker_id,
            task_id=task_id,
            started_event=started,
        )
        is None
    )
    start = next(
        item
        for item in await events.read_run(run_id)
        if isinstance(item, WorkerStarted) and item.worker_id == worker_id
    )
    assert start.lease_expires_at is not None
    expired_now = start.lease_expires_at + timedelta(seconds=1)
    async with first.worker_guard(run_id, worker_id):
        assert await second.reconcile_stale_workers(run_id, now=expired_now) == 0
    assert await second.reconcile_stale_workers(run_id, now=expired_now) == 1
    assert await first.reconcile_stale_workers(run_id, now=expired_now) == 0
    trace = await events.read_run(run_id)
    recovered = [item for item in trace if isinstance(item, WorkerLeaseRecovered)]
    assert len(recovered) == 1
    assert (
        recovered[0].released_actions,
        recovered[0].settled_model_calls,
        recovered[0].released_coverage,
    ) == (1, 1, 1)
    assert len([item for item in trace if isinstance(item, WorkerHeartbeat)]) == 0
    projection = project_controller_events(trace)
    usage = await first.snapshot(run_id)
    assert projection.active_workers == usage["active_workers"] == 0
    assert projection.reserved_tokens == usage["reserved_tokens"] == 0
    assert not projection.active_actions and not projection.active_coverage
    assert not projection.model_reservations
    assert (await world.coverage(run_id))[0].status == "released"
    with pytest.raises(ValueError, match="lease"):
        await first.settle_model_call(
            run_id,
            call_id,
            actual_input_tokens=1,
            actual_output_tokens=1,
            worker_id=worker_id,
            task_id=task_id,
        )
    another = action.model_copy(update={"action_id": uuid4()})
    assert await first.reserve_action(another, budget) == "worker_lease_expired"


@pytest.mark.postgres
async def test_spawned_worker_crash_and_setup_failure_are_replayable(controllers) -> None:
    first, second, events, _ = controllers
    run_id, worker_id, task_id = uuid4(), uuid4(), uuid4()
    budget = Budget(max_workers=2, max_concurrency=1)
    assert await first.spawn_worker(run_id, worker_id, task_id, "invoice", budget) is None
    world = EventWorldState(events)
    claim = CoverageClaim(
        claim_id=uuid4(),
        run_id=run_id,
        task_id=task_id,
        component="invoice",
        objective="object authorization",
    )
    await world.claim_coverage(claim)
    spawn = next(
        event for event in await events.read_run(run_id) if isinstance(event, WorkerSpawned)
    )
    assert spawn.lease_expires_at is not None
    assert (
        await second.reconcile_stale_workers(
            run_id, now=spawn.lease_expires_at + timedelta(seconds=1)
        )
        == 1
    )
    trace = await events.read_run(run_id)
    assert project_controller_events(trace).active_workers == 0
    assert not project_controller_events(trace).active_coverage
    assert (await world.coverage(run_id))[0].status == "released"
    assert (await first.snapshot(run_id))["active_workers"] == 0

    second_worker, second_task = uuid4(), uuid4()
    assert await first.spawn_worker(run_id, second_worker, second_task, "ticket", budget) is None
    await first.abort_spawned_worker(run_id, second_worker, second_task, "packet_failed")
    assert project_controller_events(await events.read_run(run_id)).active_workers == 0
    assert (
        await first.reconcile_stale_workers(run_id, now=spawn.lease_expires_at + timedelta(days=1))
        == 0
    )


@pytest.mark.postgres
async def test_recovery_preserves_completed_model_usage(controllers) -> None:
    first, second, events, _ = controllers
    run_id, worker_id, task_id, call_id = uuid4(), uuid4(), uuid4(), uuid4()
    budget = Budget(max_workers=1, max_concurrency=1, max_model_calls=1, max_total_tokens=100)
    assert await first.spawn_worker(run_id, worker_id, task_id, "invoice", budget) is None
    assert await first.start_worker(run_id, worker_id, task_id, budget) is None
    assert (
        await first.reserve_model_call(
            run_id,
            call_id,
            budget,
            reserved_input_tokens=30,
            reserved_output_tokens=10,
            request_bytes=80,
            worker_id=worker_id,
            task_id=task_id,
        )
        is None
    )
    await events.append(
        ModelCallCompleted(
            schema_version="1",
            run_id=run_id,
            actor="controller",
            call_id=call_id,
            worker_id=worker_id,
            task_id=task_id,
            provider_status="completed",
            tool_call_count=0,
            input_tokens=17,
            output_tokens=5,
            estimated_cost_usd=0.000023,
        )
    )
    started = next(
        event for event in await events.read_run(run_id) if isinstance(event, WorkerStarted)
    )
    assert started.lease_expires_at is not None
    assert (
        await second.reconcile_stale_workers(
            run_id, now=started.lease_expires_at + timedelta(seconds=1)
        )
        == 1
    )
    usage = await first.snapshot(run_id)
    assert usage["used_tokens"] == 22
    assert usage["used_cost_microusd"] == 23
    assert usage["reserved_tokens"] == usage["reserved_cost_microusd"] == 0
    projected = project_controller_events(await events.read_run(run_id))
    assert projected.used_tokens == usage["used_tokens"]
    assert projected.used_cost_microusd == usage["used_cost_microusd"]


@pytest.mark.postgres
async def test_future_worker_model_floor_is_reserved_atomically(controllers) -> None:
    first, second, _, _ = controllers
    run_id = uuid4()
    budget = Budget(
        max_model_calls=20,
        max_total_tokens=120000,
        max_output_tokens_per_call=128,
    )
    floor = 5 * (15500 + 128)
    first_call = uuid4()
    assert (
        await first.reserve_model_call(
            run_id,
            first_call,
            budget,
            reserved_input_tokens=24872,
            reserved_output_tokens=128,
            request_bytes=40000,
            protected_future_tokens=floor,
            protected_future_model_calls=5,
        )
        is None
    )
    assert (
        await second.reserve_model_call(
            run_id,
            uuid4(),
            budget,
            reserved_input_tokens=26872,
            reserved_output_tokens=128,
            request_bytes=18000,
            protected_future_tokens=floor,
            protected_future_model_calls=5,
        )
        == "model_token_budget_exhausted"
    )
    await first.settle_model_call(
        run_id, first_call, actual_input_tokens=14000, actual_output_tokens=100
    )
    assert (
        await second.reserve_model_call(
            run_id,
            uuid4(),
            budget,
            reserved_input_tokens=26872,
            reserved_output_tokens=128,
            request_bytes=18000,
            protected_future_tokens=floor,
            protected_future_model_calls=5,
        )
        is None
    )
