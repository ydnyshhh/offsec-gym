"""Request artifacts and evidence retain provenance without request secrets."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.events import ActionRequested
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.specs import Budget


class MemoryEvents:
    def __init__(self) -> None:
        self.events = []

    async def read_run(self, run_id: UUID) -> list:
        return [event for event in self.events if event.run_id == run_id]

    async def append(self, event: object) -> None:
        self.events.append(event)


class FakeState:
    def __init__(self, root: Path, instance_id: UUID) -> None:
        self.root = root
        self.instance_id = instance_id
        self.build_id = uuid4()
        self.generation = 0

    def instance_dir(self, instance_id: UUID) -> Path:
        assert instance_id == self.instance_id
        return self.root / instance_id.hex

    def load_instance(self, instance_id: UUID) -> SimpleNamespace:
        assert instance_id == self.instance_id
        return SimpleNamespace(build_id=self.build_id, generation=self.generation)

    def verify_build_integrity(self, build_id: UUID) -> SimpleNamespace:
        assert build_id == self.build_id
        return SimpleNamespace(spec=SimpleNamespace(family="saas"))


class FakeRuntime:
    def __init__(self, root: Path, instance_id: UUID) -> None:
        self.state = FakeState(root, instance_id)
        self.guard = asyncio.Lock()
        self.dispatch_started = asyncio.Event()
        self.finish_dispatch = asyncio.Event()
        self.dispatched = 0

    def get_instance_guard(self, instance_id: UUID) -> asyncio.Lock:
        assert instance_id == self.state.instance_id
        return self.guard

    def _public_accounts(self, build_id: UUID) -> list:
        return []

    async def instance_status(self, instance_id: UUID) -> SimpleNamespace:
        return SimpleNamespace(state="healthy")

    async def execute_saas_http(self, *args: object) -> dict[str, object]:
        self.dispatch_started.set()
        await self.finish_dispatch.wait()
        self.dispatched += 1
        body = b'{"ok":true}'
        return {
            "http_status": 200,
            "body_b64": base64.b64encode(body).decode(),
            "truncated": False,
            "content_type": "application/json",
            "redirect_location": None,
        }

    async def reset(self) -> None:
        async with self.guard:
            self.state.generation += 1


@pytest.mark.asyncio
async def test_artifacts_redact_secrets_and_bind_generation_through_reset(tmp_path: Path) -> None:
    instance_id = uuid4()
    run_id = uuid4()
    runtime = FakeRuntime(tmp_path, instance_id)
    events = MemoryEvents()
    gateway = ComposeActionGateway(runtime, events, min_interval_seconds=0)
    context = ExperimentContext(
        run_id=run_id,
        range_instance_id=instance_id,
        range_generation=0,
        budget=Budget(max_actions=10),
    )
    body = {"payload": {"token": "secret", "visible": [1, {"password": "top", "v": True}]}}
    action = ActionRequest(
        run_id=run_id,
        kind="http_request",
        destination="saas",
        method="POST",
        path="/api/echo?api_key=verysecret&visible=yes",
        json_body=body,
    )
    pending_action = asyncio.create_task(gateway.execute(action, context))
    await runtime.dispatch_started.wait()
    pending_reset = asyncio.create_task(runtime.reset())
    await asyncio.sleep(0)
    assert runtime.state.generation == 0
    runtime.finish_dispatch.set()
    result = await pending_action
    await pending_reset
    assert result.status == "completed"
    assert runtime.state.generation == 1

    requested = next(event for event in events.events if isinstance(event, ActionRequested))
    assert requested.schema_version == "2"
    assert requested.range_instance_id == instance_id
    assert requested.range_generation == 0
    assert (
        requested.body_sha256
        == hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    request_file = (
        runtime.state.instance_dir(instance_id)
        / "requests"
        / (f"{requested.request_artifact_id.hex}.json")
    )
    assert request_file.stat().st_mode & 0o777 == 0o600
    request = RequestArtifact.model_validate_json(request_file.read_bytes())
    assert request.path == "/api/echo?api_key=%2A&visible=yes"
    assert request.json_body == {
        "payload": {"token": "*", "visible": [1, {"password": "*", "v": True}]}
    }
    assert "verysecret" not in request_file.read_text()
    assert "top" not in request_file.read_text()
    evidence_file = (
        runtime.state.instance_dir(instance_id) / "evidence" / f"{result.evidence_id.hex}.json"
    )
    assert evidence_file.stat().st_mode & 0o777 == 0o600
    evidence = Evidence.model_validate_json(evidence_file.read_bytes())
    assert evidence.request_artifact_id == request.request_artifact_id
    assert evidence.range_generation == 0
    assert "json_body" not in evidence_file.read_text()
    assert "request_path" not in evidence_file.read_text()

    stale = await gateway.execute(action.model_copy(update={"action_id": uuid4()}), context)
    assert stale.status == "blocked"
    assert stale.reason_code == "range_generation_mismatch"
    assert runtime.dispatched == 1
    stale_request = [event for event in events.events if isinstance(event, ActionRequested)][-1]
    stale_file = (
        runtime.state.instance_dir(instance_id)
        / "requests"
        / (f"{stale_request.request_artifact_id.hex}.json")
    )
    assert stale_file.is_file()
    assert stale_file.stat().st_mode & 0o777 == 0o600
