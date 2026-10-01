"""Small M6.0 controller contracts that do not need PostgreSQL."""

from uuid import uuid4

import pytest

from offsecgym.providers.token_budget import estimate_input_tokens
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import (
    AgentContext,
    AgentVisibleRangeContext,
    CoverageClaim,
    EntityRef,
    WorldFact,
)
from offsecgym.schemas.events import (
    ActionAttemptReserved,
    ActionReservationAcquired,
    ActionReservationReleased,
    ModelBudgetReserved,
    ModelBudgetSettled,
    WorkerFinished,
    WorkerSpawned,
    WorkerStarted,
    parse_event,
)
from offsecgym.schemas.specs import Budget, ModelSpec
from offsecgym.solver.workers import WorkerPacketBuilder, worker_budget
from offsecgym.storage.controller import request_fingerprint
from offsecgym.storage.projection import project_controller_events


def test_fingerprint_canonicalizes_body_and_binds_identity() -> None:
    run_id, identity = uuid4(), uuid4()
    action = ActionRequest(
        run_id=run_id,
        kind="http_request",
        destination="saas",
        method="POST",
        path="/api/invoices/1",
        identity_id=identity,
        json_body={"one": 1, "two": 2},
    )
    reordered = action.model_copy(update={"json_body": {"two": 2, "one": 1}})
    assert request_fingerprint(action) == request_fingerprint(reordered)
    assert request_fingerprint(action) != request_fingerprint(
        action.model_copy(update={"identity_id": uuid4()})
    )
    assert request_fingerprint(action) != request_fingerprint(
        action.model_copy(update={"path": "/api/invoices/2"})
    )


def test_model_preflight_estimate_is_configured_and_recordable() -> None:
    model = ModelSpec(provider="openrouter", name="moonshotai/kimi-k3")
    payload = {"messages": [{"role": "user", "content": "A" * 3000}]}
    reserved, request_bytes = estimate_input_tokens(payload, model)
    assert reserved == (request_bytes + 1) // 2 + 1024
    assert reserved < request_bytes + 1024
    calibrated = model.model_copy(update={"input_reservation_bytes_per_token": 2.5})
    assert estimate_input_tokens(payload, calibrated)[0] < reserved
    event = ModelBudgetReserved(
        run_id=uuid4(),
        actor="controller",
        call_id=uuid4(),
        reserved_tokens=reserved + 8192,
        reserved_input_tokens=reserved,
        reserved_output_tokens=8192,
        request_bytes=request_bytes,
        reserved_cost_microusd=0,
    )
    assert parse_event(event.model_dump(mode="json")) == event
    with pytest.raises(ValueError, match="matching input/output split"):
        event.model_copy(update={"reserved_tokens": 1}).model_validate(
            event.model_copy(update={"reserved_tokens": 1}).model_dump()
        )


async def test_worker_packet_trims_growth_before_exceeding_handoff_cap(tmp_path) -> None:
    run_id, source_worker_id = uuid4(), uuid4()
    facts = [
        WorldFact(
            fact_id=uuid4(),
            run_id=run_id,
            kind="hypothesis",
            subject=EntityRef(entity_type="invoice", entity_id=entity_id),
            predicate=f"long_predicate_{index}",
            object_value="x" * 180,
            source_worker_id=source_worker_id,
            confidence=0.5,
        )
        for entity_id in (uuid4() for _ in range(8))
        for index in range(6)
    ]
    coverage = [
        CoverageClaim(
            claim_id=uuid4(),
            run_id=run_id,
            task_id=uuid4(),
            component="invoice",
            objective="c" * 200,
            status="completed",
        )
        for _ in range(12)
    ]

    class Events:
        async def read_run(self, run_id):
            return ()

    class World:
        async def query(self, run_id):
            return facts

        async def coverage(self, run_id):
            return coverage

    builder = WorkerPacketBuilder(Events(), tmp_path)
    builder.world = World()
    context = AgentContext(
        run_id=run_id,
        objective="invoice test",
        global_budget=Budget(max_model_calls=6),
        range=AgentVisibleRangeContext(range_instance_id=uuid4(), family="saas"),
    )
    packet = await builder.build(
        context,
        uuid4(),
        uuid4(),
        "invoice test",
        ("invoice",),
        Budget(max_model_calls=1),
    )
    assert len(packet.model_dump_json()) <= 10000
    assert packet.relevant_entities
    assert (
        packet.omitted_entity_details > 0
        or packet.omitted_hypotheses > 36
        or packet.omitted_coverage > 0
    )


def test_worker_budget_partitions_tokens_and_calls_without_exceeding_global() -> None:
    global_budget = Budget(
        max_model_calls=20,
        max_actions=60,
        max_http_requests=60,
        max_total_tokens=120000,
        max_output_tokens_per_call=8192,
    )
    slices = [worker_budget(global_budget, index) for index in range(6)]
    assert [item.max_model_calls for item in slices] == [4, 4, 3, 3, 3, 3]
    assert sum(item.max_total_tokens for item in slices) == 120000
    assert all(item.max_total_tokens == 20000 for item in slices)
    assert sum(item.max_actions for item in slices) == 60


def test_worker_actions_require_task_attribution() -> None:
    with pytest.raises(ValueError, match="task_id"):
        ActionRequest(
            run_id=uuid4(),
            worker_id=uuid4(),
            kind="http_request",
            destination="saas",
            method="GET",
            path="/api/me",
        )


def test_controller_events_round_trip_and_project_state() -> None:
    run_id, worker_id, task_id, action_id, call_id = (uuid4() for _ in range(5))
    fingerprint = "a" * 64
    trace = [
        WorkerSpawned(
            run_id=run_id,
            actor="coordinator",
            worker_id=worker_id,
            task_id=task_id,
            objective="billing",
        ),
        WorkerStarted(run_id=run_id, actor="coordinator", worker_id=worker_id, task_id=task_id),
        ActionAttemptReserved(
            run_id=run_id,
            actor="controller",
            action_id=action_id,
            worker_id=worker_id,
            task_id=task_id,
        ),
        ActionReservationAcquired(
            run_id=run_id,
            actor="controller",
            action_id=action_id,
            fingerprint=fingerprint,
            worker_id=worker_id,
            task_id=task_id,
        ),
        ActionReservationReleased(
            run_id=run_id,
            actor="controller",
            action_id=action_id,
            fingerprint=fingerprint,
            worker_id=worker_id,
            task_id=task_id,
        ),
        ModelBudgetReserved(
            schema_version="1",
            run_id=run_id,
            actor="controller",
            call_id=call_id,
            reserved_tokens=50,
            reserved_cost_microusd=60,
            worker_id=worker_id,
            task_id=task_id,
        ),
        ModelBudgetSettled(
            schema_version="1",
            run_id=run_id,
            actor="controller",
            call_id=call_id,
            actual_tokens=30,
            actual_cost_microusd=40,
            worker_id=worker_id,
            task_id=task_id,
        ),
        WorkerFinished(
            run_id=run_id,
            actor="coordinator",
            worker_id=worker_id,
            task_id=task_id,
            status="completed",
        ),
    ]
    assert [parse_event(item.model_dump(mode="json")) for item in trace] == trace
    projected = project_controller_events(trace)
    assert projected.used_actions == projected.used_http_requests == 1
    assert projected.used_model_calls == 1
    assert projected.used_tokens == 30
    assert projected.reserved_tokens == 0
    assert projected.used_cost_microusd == 40
    assert projected.spawned_workers == 1 and projected.active_workers == 0
    assert projected.worker_status[worker_id] == "finished"
    assert projected.active_actions == {}
