"""Generic witness state is reconstructed from same-object gateway proof."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_m65_witness_packet import _trace
from test_milestone_3_runner import MemoryEvents
from test_milestone_4 import QueueProvider, turn

from offsecgym.research.m65_witness_packet import (
    ReporterEvidenceBundle,
    ReporterPacket,
    TrustedAction,
    WitnessAction,
    build_reporter_bundle,
)
from offsecgym.schemas.domain import AgentContext, AgentTask, AgentVisibleRangeContext
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    RangeStarted,
    RunStarted,
    WitnessHypothesisStarted,
    parse_event,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.specs import Budget, ModelSpec
from offsecgym.solver.monolithic import MonolithicSaasAgent, model_tools
from offsecgym.worldview.witness import EventWitnessLedger, project_witness


def _action(
    run_id: UUID,
    instance_id: UUID,
    identity: UUID,
    object_id: UUID,
    *,
    sequence: int,
    method: str,
    state: str,
    http_status: int = 200,
    truncated: bool = False,
) -> tuple[WitnessAction, TrustedAction]:
    action_id, request_id, evidence_id = (uuid4() for _ in range(3))
    path = f"/api/invoices/{object_id}" + ("/refund" if method != "GET" else "")
    raw = json.dumps({"id": str(object_id), "status": state}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    request = RequestArtifact(
        request_artifact_id=request_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        identity_id=identity,
        destination="saas",
        method=method,
        path=path,
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        request_artifact_id=request_id,
        identity_id=identity,
        http_status=http_status,
        body_b64=base64.b64encode(raw).decode(),
        response_sha256=digest,
        truncated=truncated,
    )
    indexed = WitnessAction(
        action_id=action_id,
        evidence_id=evidence_id,
        request_sequence=sequence,
        completion_sequence=sequence + 1,
        method=method,
        path=path,
        identity_id=identity,
        route_target_id=object_id,
        response_object_id=object_id,
        http_status=http_status,
        response_sha256=digest,
        request_fingerprint="a" * 64,
        response_excerpt=raw.decode(),
        truncated=truncated,
    )
    return indexed, TrustedAction(request, evidence)


def _case(defect: str | None = None):
    run_id, instance_id, identity, object_id, other = (uuid4() for _ in range(5))
    before = _action(
        run_id,
        instance_id,
        other if defect == "wrong_before_identity" else identity,
        object_id,
        sequence=3,
        method="GET",
        state="paid",
    )
    action = _action(
        run_id,
        instance_id,
        identity,
        object_id,
        sequence=5,
        method="POST",
        state="refunded",
        http_status=403 if defect == "failed_action" else 200,
    )
    after = _action(
        run_id,
        instance_id,
        other if defect == "wrong_after_identity" else identity,
        other if defect == "wrong_after_object" else object_id,
        sequence=7,
        method="GET",
        state="paid" if defect == "unchanged" else "refunded",
        truncated=defect == "truncated_after",
    )
    entries = [before, action] + ([] if defect == "missing_after" else [after])
    if defect == "late_before":
        entries[0] = _action(
            run_id,
            instance_id,
            identity,
            object_id,
            sequence=9,
            method="GET",
            state="paid",
        )
        entries.sort(key=lambda item: item[0].completion_sequence)
    if defect == "intervening_mutation":
        extra = _action(
            run_id,
            instance_id,
            other,
            object_id,
            sequence=7,
            method="POST",
            state="refunded",
        )
        entries[-1] = _action(
            run_id,
            instance_id,
            identity,
            object_id,
            sequence=9,
            method="GET",
            state="refunded",
        )
        entries.insert(-1, extra)
    packet = ReporterPacket(
        run_id=run_id,
        source_build_id=uuid4(),
        source_experiment_sha256="b" * 64,
        source_trace_sha256="c" * 64,
        range_instance_id=instance_id,
        range_generation=0,
        actions=tuple(item[0] for item in entries),
        unobserved_attempts=(),
        existing_candidates=(),
    )
    bundle = ReporterEvidenceBundle(
        packet,
        {item[0].action_id: item[1] for item in entries},
    )
    hypothesis = WitnessHypothesisStarted(
        run_id=run_id,
        actor="solver",
        witness_id=uuid4(),
        identity_id=identity,
        object_id=object_id,
        range_generation=0,
        before_path=f"/api/invoices/{object_id}",
        action_method="POST",
        action_path=f"/api/invoices/{object_id}/refund",
        state_field="status",
    )
    return hypothesis, bundle


def test_complete_witness_has_exact_ordered_gateway_citations() -> None:
    hypothesis, bundle = _case()
    view = project_witness(hypothesis, bundle)
    assert view.status == "complete"
    assert view.before and view.action and view.after
    assert view.before.completion_sequence < view.action.request_sequence
    assert view.action.completion_sequence < view.after.request_sequence
    assert view.before_state == '"paid"'
    assert view.after_state == '"refunded"'
    assert parse_event(hypothesis.model_dump(mode="python")) == hypothesis


def test_cross_object_transition_keeps_state_object_explicit() -> None:
    hypothesis, bundle = _case()
    change_id = uuid4()
    old_action = bundle.packet.actions[1]
    new_path = f"/api/changes/{change_id}/deploy"
    replacement = old_action.model_copy(
        update={
            "path": new_path,
            "route_target_id": change_id,
            "response_object_id": change_id,
        }
    )
    packet = bundle.packet.model_copy(
        update={"actions": (bundle.packet.actions[0], replacement, bundle.packet.actions[2])}
    )
    cross_object = hypothesis.model_copy(
        update={"action_object_id": change_id, "action_path": new_path}
    )
    assert project_witness(
        cross_object, ReporterEvidenceBundle(packet, bundle.by_action)
    ).status == ("complete")
    assert project_witness(hypothesis, ReporterEvidenceBundle(packet, bundle.by_action)).status == (
        "action_missing"
    )


@pytest.mark.parametrize(
    ("defect", "status"),
    [
        ("wrong_before_identity", "before_missing"),
        ("wrong_after_identity", "after_missing"),
        ("wrong_after_object", "after_missing"),
        ("failed_action", "action_missing"),
        ("late_before", "before_missing"),
        ("missing_after", "after_missing"),
        ("truncated_after", "after_missing"),
        ("unchanged", "state_unchanged"),
        ("intervening_mutation", "ambiguous_intervening_action"),
    ],
)
def test_incomplete_or_ambiguous_trace_cannot_become_complete(defect, status) -> None:
    hypothesis, bundle = _case(defect)
    assert project_witness(hypothesis, bundle).status == status


@pytest.mark.asyncio
async def test_hypothesis_is_event_backed_and_model_tools_are_opt_in(tmp_path: Path) -> None:
    run_id, trace, _, target, _, _ = _trace(tmp_path)
    events = MemoryEvents()
    events.items = trace.copy()
    ledger = EventWitnessLedger(events, tmp_path)
    identity = uuid4()
    kwargs = dict(
        identity_id=identity,
        object_id=target,
        before_path=f"/api/documents/{target}",
        action_method="POST",
        action_path=f"/api/documents/{target}/change",
        state_field="status",
        causation_id=uuid4(),
    )
    view = await ledger.start(run_id, **kwargs)
    assert view.status == "before_missing"
    assert await ledger.get(run_id, view.witness_id) == view
    with pytest.raises(ValueError, match="already exists"):
        await ledger.start(run_id, **kwargs)
    with pytest.raises(ValueError, match="same exact object"):
        await ledger.start(
            run_id,
            **{**kwargs, "action_path": f"/api/documents/{uuid4()}/change"},
        )
    assert "start_witness" not in {item["name"] for item in model_tools(structured=True)}
    assert "start_witness" in {
        item["name"] for item in model_tools(structured=True, witness_planning=True)
    }


@pytest.mark.asyncio
async def test_enterprise_identity_evidence_does_not_require_saas_reporter_fields(
    tmp_path: Path,
) -> None:
    run_id, instance_id, build_id, identity, organization, object_id = (uuid4() for _ in range(6))
    action_id, request_id, evidence_id = (uuid4() for _ in range(3))
    body = json.dumps(
        {
            "id": str(identity),
            "username": "change.viewer",
            "organization_id": str(organization),
            "roles": [{"project_id": str(uuid4()), "role": "change_viewer", "active": True}],
        }
    ).encode()
    digest = hashlib.sha256(body).hexdigest()
    request = RequestArtifact(
        request_artifact_id=request_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        identity_id=identity,
        destination="enterprise_change_control_v1",
        method="GET",
        path="/api/me",
        source_phase="bootstrap",
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        request_artifact_id=request_id,
        identity_id=identity,
        http_status=200,
        body_b64=base64.b64encode(body).decode(),
        response_sha256=digest,
    )
    instance = tmp_path / "instances" / instance_id.hex
    (instance / "requests").mkdir(parents=True)
    (instance / "evidence").mkdir()
    (instance / "requests" / f"{request_id.hex}.json").write_text(request.model_dump_json())
    (instance / "evidence" / f"{evidence_id.hex}.json").write_text(evidence.model_dump_json())
    trace = [
        RunStarted(run_id=run_id, actor="controller", sequence_number=1, experiment_hash="a" * 64),
        RangeStarted(
            run_id=run_id,
            actor="controller",
            sequence_number=2,
            build_id=build_id,
            range_instance_id=instance_id,
            range_generation=0,
        ),
        ActionRequested(
            run_id=run_id,
            actor="gateway",
            sequence_number=3,
            action_id=action_id,
            action_type="http_request",
            destination="enterprise_change_control_v1",
            method="GET",
            path_sha256=hashlib.sha256(b"/api/me").hexdigest(),
            range_instance_id=instance_id,
            range_generation=0,
            request_artifact_id=request_id,
            identity_id=identity,
            source_phase="bootstrap",
        ),
        ActionCompleted(
            run_id=run_id,
            actor="gateway",
            sequence_number=4,
            action_id=action_id,
            evidence_id=evidence_id,
            duration_ms=1,
            http_status=200,
            response_sha256=digest,
        ),
    ]
    with pytest.raises(ValueError, match="trusted identity response is malformed"):
        build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    bundle = build_reporter_bundle(
        trace, tmp_path, expected_run_id=run_id, project_visible_identities=False
    )
    assert bundle.packet.identities == ()
    assert bundle.get_action(action_id)["response_sha256"] == digest
    evidence_path = instance / "evidence" / f"{evidence_id.hex}.json"
    evidence_path.write_text(evidence.model_copy(update={"body_b64": "e30="}).model_dump_json())
    with pytest.raises(ValueError, match="evidence response hash mismatch"):
        build_reporter_bundle(
            trace, tmp_path, expected_run_id=run_id, project_visible_identities=False
        )
    evidence_path.write_text(evidence.model_dump_json())
    events = MemoryEvents()
    events.items = trace.copy()
    ledger = EventWitnessLedger(events, tmp_path)
    view = await ledger.start(
        run_id,
        identity_id=identity,
        object_id=object_id,
        before_path=f"/api/changes/{object_id}",
        action_method="POST",
        action_path=f"/api/changes/{object_id}/deploy",
        state_field="status",
        causation_id=uuid4(),
    )
    assert view.status == "before_missing"
    assert await ledger.get(run_id, view.witness_id) == view


@pytest.mark.asyncio
async def test_fake_provider_receives_bounded_witness_reminder_without_http(tmp_path: Path) -> None:
    run_id, instance_id, build_id, identity, object_id = (uuid4() for _ in range(5))
    events = MemoryEvents()
    await events.append(RunStarted(run_id=run_id, actor="controller", experiment_hash="a" * 64))
    await events.append(
        RangeStarted(
            run_id=run_id,
            actor="controller",
            build_id=build_id,
            range_instance_id=instance_id,
            range_generation=0,
        )
    )
    tool_call = {
        "type": "function_call",
        "name": "start_witness",
        "call_id": "witness-1",
        "arguments": json.dumps(
            {
                "identity_id": str(identity),
                "object_id": str(object_id),
                "before_path": f"/api/invoices/{object_id}",
                "action_method": "POST",
                "action_path": f"/api/invoices/{object_id}/refund",
                "state_field": "status",
            }
        ),
    }
    provider = QueueProvider(turn(tool_call), turn())

    class NoHttpTools:
        async def execute(self, action):
            raise AssertionError("witness planning must not dispatch HTTP")

    agent = MonolithicSaasAgent(
        provider,
        ModelSpec(provider="fake", name="fake"),
        SimpleNamespace(),
        events,
        tmp_path,
        memory="structured",
        witness_planning=True,
    )
    budget = Budget(max_total_tokens=5_000, max_model_calls=2, max_output_tokens_per_call=128)
    task = AgentTask(task_id=uuid4(), goal="Test workflow state", budget=budget)
    context = AgentContext(
        run_id=run_id,
        objective=task.goal,
        range=AgentVisibleRangeContext(
            range_instance_id=instance_id, range_generation=0, family="saas"
        ),
        global_budget=budget,
    )
    result = await agent.run(task, context, NoHttpTools())
    assert result.status == "completed"
    hypotheses = [x for x in events.items if isinstance(x, WitnessHypothesisStarted)]
    assert len(hypotheses) == 1
    assert "witness=" in str(provider.requests[1]["input"][-1]["content"])
    assert "start_witness" in {tool["name"] for tool in provider.requests[0]["tools"]}
    assert "http_request" in {tool["name"] for tool in provider.requests[0]["tools"]}
