"""Small controller and worker handoff contracts that do not need PostgreSQL."""

import hashlib
import json
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
    ActionCompleted,
    ActionRequested,
    ActionReservationAcquired,
    ActionReservationReleased,
    ModelBudgetReserved,
    ModelBudgetSettled,
    WorkerFinished,
    WorkerSpawned,
    WorkerStarted,
    parse_event,
)
from offsecgym.schemas.evidence import RequestArtifact
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


async def test_packet_retains_objective_entities_and_exact_post_body(tmp_path) -> None:
    run_id, instance_id, source_worker, identity_id = (uuid4() for _ in range(4))
    action_id, evidence_id, artifact_id = (uuid4() for _ in range(3))
    target_id = uuid4()
    path = f"/api/invoices/{target_id}/refund"
    body = {"reason": "duplicate charge"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    artifact = RequestArtifact(
        request_artifact_id=artifact_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        worker_id=source_worker,
        task_id=uuid4(),
        identity_id=identity_id,
        destination="saas",
        method="POST",
        path=path,
        json_body=body,
    )
    folder = tmp_path / "instances" / instance_id.hex / "requests"
    folder.mkdir(parents=True)
    (folder / f"{artifact_id.hex}.json").write_text(artifact.model_dump_json())
    trace = (
        ActionRequested(
            run_id=run_id,
            actor="gateway",
            action_id=action_id,
            action_type="http_request",
            destination="saas",
            method="POST",
            path_sha256=hashlib.sha256(path.encode()).hexdigest(),
            worker_id=source_worker,
            task_id=artifact.task_id,
            identity_id=identity_id,
            body_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
            range_instance_id=instance_id,
            range_generation=0,
            request_artifact_id=artifact_id,
        ),
        ActionReservationAcquired(
            run_id=run_id,
            actor="controller",
            action_id=action_id,
            fingerprint="a" * 64,
            worker_id=source_worker,
            task_id=artifact.task_id,
        ),
        ActionCompleted(
            run_id=run_id,
            actor="gateway",
            action_id=action_id,
            worker_id=source_worker,
            task_id=artifact.task_id,
            evidence_id=evidence_id,
            duration_ms=1,
            http_status=200,
        ),
    )
    facts = [
        WorldFact(
            fact_id=uuid4(),
            run_id=run_id,
            kind="observation",
            subject=EntityRef(entity_type=kind, entity_id=entity_id),
            predicate="role" if kind == "identity" else "status",
            object_value="member" if kind == "identity" else "known",
            source_worker_id=source_worker,
            confidence=1,
        )
        for kind, count in (("invoice", 12), ("identity", 3))
        for entity_id in (uuid4() for _ in range(count))
    ]
    facts.append(
        WorldFact(
            fact_id=uuid4(),
            run_id=run_id,
            kind="observation",
            subject=EntityRef(entity_type="invoice", entity_id=target_id),
            predicate="status",
            object_value="refunded",
            source_worker_id=source_worker,
            source_action_ids=(action_id,),
            evidence_ids=(evidence_id,),
            confidence=1,
        )
    )

    class Events:
        async def read_run(self, run_id):
            return trace

    class World:
        async def query(self, run_id):
            return facts

        async def coverage(self, run_id):
            return ()

    builder = WorkerPacketBuilder(Events(), tmp_path)
    builder.world = World()
    packet = await builder.build(
        AgentContext(
            run_id=run_id,
            objective="refund",
            range=AgentVisibleRangeContext(range_instance_id=instance_id, family="saas"),
        ),
        uuid4(),
        uuid4(),
        "Test invoice refund transition authorization",
        ("invoice", "identity"),
        Budget(max_model_calls=1),
    )
    assert target_id in {item.entity_id for item in packet.relevant_entities}
    assert len([item for item in packet.relevant_entities if item.entity_type == "identity"]) >= 2
    checked = packet.prior_checked_actions[0]
    assert checked.body_sha256 == hashlib.sha256(canonical.encode()).hexdigest()
    assert checked.body_json == canonical
    assert len(packet.model_dump_json()) <= 10000


def test_worker_budget_uses_rolling_future_worker_floors() -> None:
    global_budget = Budget(
        max_model_calls=20,
        max_actions=60,
        max_http_requests=60,
        max_total_tokens=120000,
        max_output_tokens_per_call=8192,
    )
    first = worker_budget(global_budget, 0)
    assert first.max_model_calls == 15
    assert first.max_total_tokens == 120000 - 5 * (15500 + 4000)
    assert first.max_output_tokens_per_call == 4000
    assert first.max_actions == first.max_http_requests == 55
    second = worker_budget(
        global_budget,
        1,
        {"used_model_calls": 3, "used_tokens": 26000, "used_actions": 8, "used_http_requests": 7},
    )
    assert second.max_model_calls == 13
    assert second.max_total_tokens == 120000 - 26000 - 4 * (15500 + 4000)
    assert second.max_actions == 48
    assert second.max_http_requests == 49
    final = worker_budget(global_budget, 5, {"used_model_calls": 15, "used_tokens": 90000})
    assert final.max_model_calls == 5
    assert final.max_total_tokens == 30000


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
