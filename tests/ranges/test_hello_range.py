"""Real Docker proof of lifecycle, network containment, and gateway audit."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError, run_command
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.events import ActionBlocked, ActionCompleted, ActionRequested
from offsecgym.schemas.specs import Budget, RangeSpec
from offsecgym.storage.event_store import PostgresEventStore


class MemoryEventStore:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        persisted = event.model_copy(update={"sequence_number": len(self.items) + 1})
        self.items.append(persisted)
        return persisted

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
            pytest.fail(f"Docker required by CI: {result.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")


async def service_container(project_name: str, service: str) -> str:
    return (
        await run_command(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--format",
                "{{.ID}}",
            ]
        )
    ).strip()


@pytest.mark.docker
async def test_hello_lifecycle_containment_and_gateway(tmp_path: Path) -> None:
    require_docker()
    runtime = ComposeRangeRuntime(tmp_path)
    spec = RangeSpec(family="hello", scenario="health_check", seed=42, topology={"hello": True})
    build_id = await runtime.build(spec)
    assert await runtime.build(spec) == build_id
    instances: list[UUID] = []
    engine = None
    try:
        first = await runtime.start(build_id)
        instances.append(first.range_id)
        second = await runtime.start(build_id)
        instances.append(second.range_id)
        assert first.state == second.state == "healthy"
        assert first.range_id != second.range_id
        first_manifest = runtime.state.load_instance(first.range_id)
        second_manifest = runtime.state.load_instance(second.range_id)
        assert first_manifest.nonce != second_manifest.nonce
        assert first_manifest.project_name != second_manifest.project_name

        first_hello = await service_container(first_manifest.project_name, "hello")
        second_hello = await service_container(second_manifest.project_name, "hello")
        assert first_hello and second_hello
        published = json.loads(
            await run_command(
                ["docker", "inspect", first_hello, "--format", "{{json .NetworkSettings.Ports}}"]
            )
        )
        assert all(binding is None for binding in published.values())
        assert (
            await run_command(
                [
                    "docker",
                    "network",
                    "inspect",
                    f"{first_manifest.project_name}_range",
                    "--format",
                    "{{.Internal}}",
                ]
            )
        ).strip() == "true"

        second_ip = (
            await run_command(
                [
                    "docker",
                    "inspect",
                    second_hello,
                    "--format",
                    "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                ]
            )
        ).strip()
        with pytest.raises(DockerCommandError):
            await runtime._compose(
                first_manifest,
                "exec",
                "-T",
                "gateway",
                "python",
                "-c",
                f"import socket; socket.create_connection(('{second_ip}',8080),2)",
                timeout_seconds=8,
            )
        with pytest.raises(DockerCommandError):
            await runtime._compose(
                first_manifest,
                "exec",
                "-T",
                "gateway",
                "python",
                "-c",
                "import socket; socket.create_connection(('1.1.1.1',443),2)",
                timeout_seconds=8,
            )

        url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
        if url:
            engine = create_async_engine(url)
            events = PostgresEventStore(engine)
        else:
            events = MemoryEventStore()
        run_id = uuid4()
        gateway = ComposeActionGateway(runtime, events, min_interval_seconds=0)
        context = ExperimentContext(
            run_id=run_id,
            range_id=first.range_id,
            budget=Budget(max_actions=20, max_http_requests=2),
        )
        action = ActionRequest(
            run_id=run_id, kind="http_request", destination="hello", method="GET", path="/hello"
        )
        result = await gateway.execute(action, context)
        assert result.status == "completed" and result.http_status == 200
        assert json.loads(result.body_text or "{}")["seed"] == 42
        evidence = (
            runtime.state.instance_dir(first.range_id)
            / "evidence"
            / f"{result.evidence_id.hex}.json"
        )
        assert evidence.is_file() and evidence.stat().st_mode & 0o777 == 0o600

        redirect = await gateway.execute(
            action.model_copy(update={"action_id": uuid4(), "path": "/redirect-out"}), context
        )
        assert redirect.http_status == 302
        assert redirect.redirect_location == "https://example.invalid/offsecgym"
        blocked = await gateway.execute(
            action.model_copy(update={"action_id": uuid4(), "destination": "outside"}), context
        )
        assert blocked.status == "blocked" and blocked.reason_code == "destination_out_of_scope"
        exhausted = await gateway.execute(action.model_copy(update={"action_id": uuid4()}), context)
        assert exhausted.status == "blocked" and exhausted.reason_code == "http_budget_exhausted"
        trace = await events.read_run(run_id)
        assert [event.sequence_number for event in trace] == list(range(1, len(trace) + 1))
        assert sum(isinstance(event, ActionRequested) for event in trace) == 4
        assert sum(isinstance(event, ActionCompleted) for event in trace) == 2
        assert sum(isinstance(event, ActionBlocked) for event in trace) == 2

        rate_run_id = uuid4()
        rate_context = ExperimentContext(
            run_id=rate_run_id, range_id=first.range_id, budget=Budget(max_actions=3)
        )
        rate_gateway = ComposeActionGateway(runtime, events, min_interval_seconds=60)
        rate_action = action.model_copy(update={"run_id": rate_run_id, "action_id": uuid4()})
        assert (await rate_gateway.execute(rate_action, rate_context)).status == "completed"
        rate_blocked = await rate_gateway.execute(
            rate_action.model_copy(update={"action_id": uuid4()}), rate_context
        )
        assert rate_blocked.status == "blocked" and rate_blocked.reason_code == "rate_limited"

        assert (await runtime.stop(first.range_id)).state == "stopped"
        assert (await runtime.stop(first.range_id)).state == "stopped"
        assert (await runtime.start(first.range_id)).state == "healthy"
        assert (await runtime.reset(first.range_id)).state == "healthy"
        assert (await runtime.destroy(first.range_id)).state == "destroyed"
        assert (await runtime.destroy(first.range_id)).state == "destroyed"
        assert (await runtime.status(second.range_id)).state == "healthy"
    finally:
        for instance_id in instances:
            await runtime.destroy(instance_id)
        if engine is not None:
            await engine.dispose()
