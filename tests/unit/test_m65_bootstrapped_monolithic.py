"""The new monolithic arm consumes audited bootstrap state before its first turn."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError
from test_milestone_3_runner import MemoryEvents

from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import AgentContext, AgentTask, AgentVisibleRangeContext
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    PrerequisiteBootstrapCompleted,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.specs import Budget, ExperimentSpec
from offsecgym.solver.bootstrap_context import bootstrap_working_set
from offsecgym.solver.monolithic import build_context
from offsecgym.solver.scripted import ExperimentInfrastructureError
from offsecgym.worldview import EventWorldState, WorldContextBuilder


def _config() -> dict:
    path = (
        Path(__file__).parents[2]
        / "experiments"
        / "configs"
        / "kimi-k3-m65-bootstrapped-monolithic.yaml"
    )
    return yaml.safe_load(path.read_text())


def test_bootstrapped_monolithic_contract_is_separate_from_legacy_monolithic() -> None:
    raw = _config()
    spec = ExperimentSpec.model_validate(raw)
    assert spec.orchestrator == "bootstrapped_monolithic"
    assert spec.bootstrap_budget is not None
    assert spec.budget.max_actions == 60
    assert spec.bootstrap_budget.max_actions == 32
    for field, value in (("memory", "transcript"), ("surface_visibility", "black_box")):
        with pytest.raises(ValidationError):
            ExperimentSpec.model_validate({**raw, field: value})
    with pytest.raises(ValidationError, match="explicit bootstrap budget"):
        ExperimentSpec.model_validate({**raw, "bootstrap_budget": None})
    with pytest.raises(ValidationError, match="only valid for bootstrapped"):
        ExperimentSpec.model_validate({**raw, "orchestrator": "monolithic"})


@pytest.mark.asyncio
async def test_bootstrap_context_replays_checked_action_and_cited_facts(tmp_path: Path) -> None:
    events = MemoryEvents()
    run_id, instance_id, identity_id, workspace_id = (uuid4() for _ in range(4))
    request = ActionRequest(
        run_id=run_id,
        source_phase="bootstrap",
        identity_id=identity_id,
        kind="http_request",
        destination="saas",
        method="GET",
        path="/api/me",
    )
    artifact_id, evidence_id = uuid4(), uuid4()
    instance = tmp_path / "instances" / instance_id.hex
    (instance / "requests").mkdir(parents=True)
    (instance / "evidence").mkdir()
    request_artifact = RequestArtifact(
        request_artifact_id=artifact_id,
        run_id=run_id,
        action_id=request.action_id,
        range_instance_id=instance_id,
        range_generation=0,
        source_phase="bootstrap",
        identity_id=identity_id,
        destination="saas",
        method="GET",
        path="/api/me",
    )
    (instance / "requests" / f"{artifact_id.hex}.json").write_text(
        request_artifact.model_dump_json()
    )
    body = json.dumps({"id": str(identity_id), "role": "member", "workspace_id": str(workspace_id)})
    digest = hashlib.sha256(body.encode()).hexdigest()
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        action_id=request.action_id,
        range_instance_id=instance_id,
        range_generation=0,
        request_artifact_id=artifact_id,
        identity_id=identity_id,
        http_status=200,
        body_b64=base64.b64encode(body.encode()).decode(),
        response_sha256=digest,
    )
    (instance / "evidence" / f"{evidence_id.hex}.json").write_text(evidence.model_dump_json())
    await events.append(
        ActionRequested(
            run_id=run_id,
            actor="gateway",
            action_id=request.action_id,
            action_type="http_request",
            destination="saas",
            method="GET",
            path_sha256=hashlib.sha256(b"/api/me").hexdigest(),
            source_phase="bootstrap",
            identity_id=identity_id,
            range_instance_id=instance_id,
            range_generation=0,
            request_artifact_id=artifact_id,
        )
    )
    await events.append(
        ActionCompleted(
            run_id=run_id,
            actor="gateway",
            action_id=request.action_id,
            evidence_id=evidence_id,
            duration_ms=1,
            http_status=200,
            response_sha256=digest,
        )
    )
    result = ActionResult(
        action_id=request.action_id,
        status="completed",
        evidence_id=evidence_id,
        duration_ms=1,
        http_status=200,
        body_text=body,
        response_sha256=digest,
    )
    await EventWorldState(events).record_response(request, result)
    await events.append(
        PrerequisiteBootstrapCompleted(
            run_id=run_id,
            actor="controller",
            snapshot_hash="a" * 64,
            identity_count=1,
            workspace_count=1,
            document_count=0,
            invoice_count=0,
            ticket_count=0,
            action_count=1,
            http_request_count=1,
        )
    )
    working = await bootstrap_working_set(events, tmp_path, run_id)
    rendered = await WorldContextBuilder(EventWorldState(events), events).build(
        run_id, "authorization", working_set=working
    )
    assert f"identity:{identity_id} role=member workspace={workspace_id}" in rendered.text
    assert f"action={request.action_id} evidence={evidence_id}" in rendered.text
    spec = ExperimentSpec.model_validate(_config())
    task = AgentTask(task_id=uuid4(), goal="Test authorization", budget=spec.budget)
    context = AgentContext(
        run_id=run_id,
        objective=task.goal,
        global_budget=Budget(max_actions=92, max_http_requests=92, max_total_tokens=120000),
        range=AgentVisibleRangeContext(
            range_instance_id=instance_id,
            family="saas",
            identity_ids=(identity_id,),
        ),
    )
    _, prompt = build_context(task, context, structured=True, bootstrap_context=True)
    assert "controller already checked identities" in prompt[0]["content"]
    assert "Discover roles through /api/me" not in prompt[0]["content"]
    (instance / "requests" / f"{artifact_id.hex}.json").write_text(
        request_artifact.model_copy(update={"path": "/api/other"}).model_dump_json()
    )
    with pytest.raises(ExperimentInfrastructureError, match="provenance_mismatch"):
        await bootstrap_working_set(events, tmp_path, run_id)
