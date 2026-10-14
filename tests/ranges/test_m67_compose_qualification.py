"""Real gateway/artifact qualification for M6.7A, with no model provider."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.reporting.provenance import EvidenceCatalog, preflight_finding
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.enterprise import fixture_for_seed
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.events import ActionCompleted, ActionRequested, RunCompleted
from offsecgym.schemas.specs import Budget, RangeSpec
from offsecgym.solver.monolithic import EnterpriseFindingArgs, TransitionFindingArgs


class MemoryEvents:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        sequence = sum(item.run_id == event.run_id for item in self.items) + 1
        stored = event.model_copy(update={"sequence_number": sequence})
        self.items.append(stored)
        return stored

    async def read_run(self, run_id: UUID):
        return [event for event in self.items if event.run_id == run_id]


def _require_docker() -> None:
    status = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if status.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required for M6.7 qualification: {status.stderr[-400:]}")
        pytest.skip("Docker daemon unavailable")


def _receipt(family: str, cases: list[str], artifact_hashes: list[str]) -> None:
    directory = os.getenv("OFFSECGYM_M67_QUALIFICATION_RECEIPT")
    if directory:
        path = Path(directory) / f"{family}.json"
        path.write_text(
            json.dumps(
                {"family": family, "cases": cases, "artifact_sha256": artifact_hashes},
                sort_keys=True,
            )
            + "\n"
        )


@pytest.mark.docker
@pytest.mark.parametrize("family", ["saas", "enterprise_change_control_v1"])
async def test_real_compose_catalog_preflight_and_isolation(tmp_path: Path, family: str) -> None:
    _require_docker()
    filename = "saas-range.yaml" if family == "saas" else "enterprise-change-control.yaml"
    spec = RangeSpec.model_validate(
        yaml.safe_load((Path(__file__).parents[2] / "examples" / filename).read_text())
    )
    runtime = ComposeRangeRuntime(tmp_path)
    events = MemoryEvents()
    build_id = await runtime.build(spec)
    instance_id = await runtime.create_instance(build_id)
    try:
        status = await runtime.start_instance(instance_id)
        fixture = json.loads((runtime.state.build_dir(build_id) / "fixture.json").read_text())
        if family == "saas":
            identity = (await runtime.snapshot_metadata(instance_id)).identities[0].identity_id
            asset = fixture["invoices"][0]["id"]
            actions = [
                ("GET", "/api/me", None),
                ("POST", f"/api/invoices/{asset}/refund", {"reason": "duplicate charge"}),
                ("GET", f"/api/invoices/{asset}", None),
            ]
            finding_schema = TransitionFindingArgs
            finding_fields = {
                "subject_role": "member",
                "action": "POST /api/invoices/{id}/refund",
                "resource_type": "invoice",
                "object_relation": "own_workspace",
                "from_state": "paid",
                "to_state": "refunded",
                "expected": "deny",
                "allowed_roles": ["workspace_admin", "platform_admin"],
            }
        else:
            _, anchors = fixture_for_seed(spec.seed)
            identity = UUID(anchors["b3_actor"])
            asset = anchors["b3_change"]
            actions = [
                ("GET", "/api/me", None),
                ("POST", f"/api/changes/{asset}/cancel", {}),
                ("GET", f"/api/changes/{asset}", None),
            ]
            finding_schema = EnterpriseFindingArgs
            finding_fields = {
                "subject_role": "operator",
                "action": "POST /api/changes/{id}/cancel",
                "resource_type": "change_request",
                "from_state": "approved",
                "to_state": "cancelled",
                "expected_behavior": "deny",
                "observed_behavior": "cancelled",
            }
        run_id, task_id, other_run, other_task = uuid4(), uuid4(), uuid4(), uuid4()
        gateway = ComposeActionGateway(runtime, events, min_interval_seconds=0)

        def context(for_run):
            return ExperimentContext(
                run_id=for_run,
                range_instance_id=instance_id,
                range_generation=status.generation,
                budget=Budget(max_actions=20, max_http_requests=20),
                allowed_identity_ids=(identity,),
            )

        async def execute(for_run, for_task, method, path, body=None, *, trusted=False):
            return await gateway.execute(
                ActionRequest(
                    run_id=for_run,
                    kind="http_request",
                    destination=family,
                    method=method,
                    path=path,
                    identity_id=identity,
                    json_body=body,
                    source_phase="bootstrap" if trusted else None,
                    task_id=None if trusted else for_task,
                    originating_call_id=None if trusted else uuid4(),
                    originating_tool_call_id=None if trusted else "scripted-no-model",
                ),
                context(for_run),
            )

        trusted = await execute(run_id, task_id, "GET", "/api/me", trusted=True)
        assert trusted.status == "completed"
        owned = [await execute(run_id, task_id, *action) for action in actions]
        assert all(item.status == "completed" for item in owned)
        other_task_result = await execute(run_id, other_task, "GET", "/api/me")
        other_run_result = await execute(other_run, task_id, "GET", "/api/me")
        assert other_task_result.status == other_run_result.status == "completed"
        entries = await EvidenceCatalog(events, tmp_path).entries(
            run_id,
            instance_id,
            status.generation,
            family,
            task_id=task_id,
            worker_id=None,
        )
        assert [item.action_id for item in entries] == [item.action_id for item in owned]
        assert [item.evidence_id for item in entries] == [item.evidence_id for item in owned]
        assert trusted.evidence_id not in {item.evidence_id for item in entries}
        assert other_task_result.evidence_id not in {item.evidence_id for item in entries}
        assert other_run_result.evidence_id not in {item.evidence_id for item in entries}
        assert entries[1].canonical_route_shape == finding_fields["action"]
        assert entries[0].canonical_route_shape == "GET /api/me"
        trace = await events.read_run(run_id)
        completed = {
            event.action_id: event for event in trace if isinstance(event, ActionCompleted)
        }
        requests = {event.action_id: event for event in trace if isinstance(event, ActionRequested)}
        artifact_hashes = []
        for entry in entries:
            done, requested = completed[entry.action_id], requests[entry.action_id]
            assert done.evidence_id == entry.evidence_id
            directory = runtime.state.instance_dir(instance_id)
            request_path = directory / "requests" / f"{requested.request_artifact_id.hex}.json"
            evidence_path = directory / "evidence" / f"{done.evidence_id.hex}.json"
            artifact_hashes.extend(
                hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (request_path, evidence_path)
            )
        arguments = {
            "claim": "Observed a state transition",
            "asset_id": asset,
            "evidence": [
                {
                    "action_id": str(owned[1].action_id),
                    "evidence_id": str(owned[1].evidence_id),
                    "description": "Observed gateway response",
                }
            ],
            "root_cause_hypothesis": None,
            **finding_fields,
        }
        preflight = preflight_finding(
            json.dumps(arguments), finding_schema, entries, family, max_chars=4000
        )
        assert preflight["ok"] is True
        assert preflight["canonical_arguments"] == arguments
        uncited = {
            **arguments,
            "evidence": [
                {
                    "action_id": str(owned[2].action_id),
                    "evidence_id": str(owned[2].evidence_id),
                    "description": "Read-only follow-up",
                }
            ],
        }
        uncited_preflight = preflight_finding(
            json.dumps(uncited), finding_schema, entries, family, max_chars=4000
        )
        assert uncited_preflight["ok"] is False
        assert "declared_action_not_cited" in uncited_preflight["errors"]
        assert "matched_root_cause_id" not in json.dumps(preflight)
        public_response = json.dumps([item.public() for item in entries]) + json.dumps(preflight)
        assert not any(
            root_label in public_response
            for root_label in ("B2-REVOKED-ROLE", "B3-CANCELLED-JOB", "B3-JOB-PROPAGATION")
        )
        await events.append(RunCompleted(run_id=run_id, actor="controller", status="completed"))
        # The gateway can still be called with a terminal context; producing a
        # post-terminal request would violate the valid-trace contract. Its
        # catalog exclusion is covered by the focused unit test instead.
        _receipt(
            family,
            [
                "real_gateway_artifact_binding",
                "public_route_and_same_run_preflight",
                "trusted_setup_excluded",
                "cross_task_and_run_excluded",
            ],
            artifact_hashes,
        )
    finally:
        await runtime.destroy_instance(instance_id)
