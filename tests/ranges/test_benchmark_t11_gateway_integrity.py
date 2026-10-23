"""A1 synthetic gateway integrity using only harmless account and denied requests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.benchmark.runtime_prerequisites import _project_removed
from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.events import ActionBlocked, ActionCompleted, ActionRequested
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.specs import Budget


class MemoryEvents:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        saved = event.model_copy(update={"sequence_number": len(self.items) + 1})
        self.items.append(saved)
        return saved

    async def read_run(self, run_id: UUID):
        return [event for event in self.items if event.run_id == run_id]


def require_docker() -> None:
    result = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail("Docker daemon required for this test")
        pytest.skip("Docker daemon is unavailable")


@pytest.mark.docker
async def test_a1_benign_gateway_binding_and_policy(tmp_path: Path) -> None:
    require_docker()
    manifest = make_task_manifest("A1", task_mode="scoped_audit", variant="vulnerable", seed=42)
    runtime = ComposeRangeRuntime(tmp_path)
    build_id = await runtime.build(manifest.private.range_spec)
    instance_id = await runtime.create_instance(build_id)
    project_name = runtime.state.load_instance(instance_id).project_name
    try:
        status = await runtime.start_instance(instance_id)
        assert status.state == "healthy" and status.generation == 0
        metadata = await runtime.snapshot_metadata(instance_id)
        assert metadata.instance_id == instance_id and metadata.seed == 42
        assert metadata.identities
        identity_id = metadata.identities[0].identity_id

        events = MemoryEvents()
        gateway = ComposeActionGateway(runtime, events, min_interval_seconds=0)
        run_id = uuid4()
        context = ExperimentContext(
            run_id=run_id,
            range_instance_id=instance_id,
            range_generation=0,
            budget=Budget(max_actions=10, max_http_requests=2),
            allowed_identity_ids=(identity_id,),
        )
        base = ActionRequest(
            run_id=run_id,
            kind="http_request",
            destination="saas",
            method="GET",
            path="/api/me",
            identity_id=identity_id,
        )

        dispatches = 0
        original_dispatch = runtime.execute_saas_http

        async def counted_dispatch(*args, **kwargs):
            nonlocal dispatches
            dispatches += 1
            return await original_dispatch(*args, **kwargs)

        runtime.execute_saas_http = counted_dispatch
        allowed = await gateway.execute(base, context)
        assert allowed.status == "completed" and allowed.http_status == 200
        assert allowed.evidence_id is not None and dispatches == 1
        assert json.loads(allowed.body_text or "{}")

        trace = await events.read_run(run_id)
        requested = next(
            event
            for event in trace
            if isinstance(event, ActionRequested) and event.action_id == base.action_id
        )
        completed = next(
            event
            for event in trace
            if isinstance(event, ActionCompleted) and event.action_id == base.action_id
        )
        assert requested.range_instance_id == instance_id
        assert requested.range_generation == 0 and requested.run_id == run_id
        assert completed.evidence_id == allowed.evidence_id
        request_path = (
            runtime.state.instance_dir(instance_id)
            / "requests"
            / f"{requested.request_artifact_id.hex}.json"
        )
        evidence_path = (
            runtime.state.instance_dir(instance_id) / "evidence" / f"{allowed.evidence_id.hex}.json"
        )
        assert request_path.stat().st_mode & 0o777 == 0o600
        assert evidence_path.stat().st_mode & 0o777 == 0o600
        request = RequestArtifact.model_validate_json(request_path.read_bytes())
        evidence = Evidence.model_validate_json(evidence_path.read_bytes())
        assert request.action_id == evidence.action_id == base.action_id
        assert request.run_id == evidence.run_id == run_id
        assert request.range_instance_id == evidence.range_instance_id == instance_id
        assert request.range_generation == evidence.range_generation == 0
        assert evidence.request_artifact_id == request.request_artifact_id
        assert evidence.response_sha256 == allowed.response_sha256

        denied = (
            ("login_is_gateway_managed", {"path": "/api/login"}),
            ("destination_out_of_scope", {"destination": "synthetic-unapproved"}),
            ("identity_out_of_scope", {"identity_id": uuid4()}),
            ("run_mismatch", {"run_id": uuid4()}),
        )
        for expected_reason, changes in denied:
            action = base.model_copy(update={"action_id": uuid4(), **changes})
            result = await gateway.execute(action, context)
            assert result.status == "blocked" and result.reason_code == expected_reason
            assert result.evidence_id is None and dispatches == 1
            assert any(
                isinstance(event, ActionBlocked)
                and event.action_id == action.action_id
                and event.reason_code == expected_reason
                for event in await events.read_run(run_id)
            )

        stale = await gateway.execute(
            base.model_copy(update={"action_id": uuid4()}),
            context.model_copy(update={"range_generation": 1}),
        )
        assert stale.status == "blocked" and stale.reason_code == "range_generation_mismatch"
        assert stale.evidence_id is None and dispatches == 1

        budget_run = uuid4()
        limited = context.model_copy(
            update={"run_id": budget_run, "budget": Budget(max_actions=1, max_http_requests=1)}
        )
        budget_action = base.model_copy(update={"action_id": uuid4(), "run_id": budget_run})
        assert (await gateway.execute(budget_action, limited)).status == "completed"
        assert dispatches == 2
        duplicate = await gateway.execute(budget_action, limited)
        assert duplicate.status == "blocked" and duplicate.reason_code == "duplicate_action"
        exhausted = await gateway.execute(
            budget_action.model_copy(update={"action_id": uuid4()}), limited
        )
        assert exhausted.status == "blocked" and exhausted.reason_code == "action_budget_exhausted"
        assert dispatches == 2

        http_run = uuid4()
        http_limited = context.model_copy(
            update={"run_id": http_run, "budget": Budget(max_actions=3, max_http_requests=1)}
        )
        http_action = base.model_copy(update={"action_id": uuid4(), "run_id": http_run})
        assert (await gateway.execute(http_action, http_limited)).status == "completed"
        http_exhausted = await gateway.execute(
            http_action.model_copy(update={"action_id": uuid4()}), http_limited
        )
        assert http_exhausted.status == "blocked"
        assert http_exhausted.reason_code == "http_budget_exhausted"
        assert dispatches == 3
    finally:
        assert (await runtime.destroy_instance(instance_id)).state == "destroyed"
        assert await _project_removed(project_name)
