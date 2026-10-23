"""Benign Docker lifecycle prerequisites; no security action or finding is executed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from offsecgym.benchmark.manifests import source_pins
from offsecgym.benchmark.tasks import TaskManifest
from offsecgym.runtime.compose import ComposeRangeRuntime, run_command
from offsecgym.schemas.common import StrictModel

CHECKS = (
    "source_and_build_integrity",
    "two_healthy_instances",
    "instance_identity_isolation",
    "internal_network_and_no_published_ports",
    "reset_generation_and_sibling_isolation",
    "teardown",
)


class RuntimePrerequisiteRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    task_id: str
    archetype_id: str
    seed: int = Field(ge=0)
    variant: str
    checks: dict[str, bool]
    status: Literal["pass", "fail"]
    failure_class: str | None = None
    real_docker_started: bool
    q2_gate: Literal["not_evaluated"] = "not_evaluated"
    q2_missing: tuple[str, ...] = (
        "task-specific gateway route-policy proof",
        "active external-egress verification",
    )
    provider_calls: Literal[0] = 0

    @model_validator(mode="after")
    def supported_status(self) -> RuntimePrerequisiteRecord:
        if set(self.checks) != set(CHECKS):
            raise ValueError("all runtime prerequisite checks are required")
        if self.status == "pass" and (not all(self.checks.values()) or self.failure_class):
            raise ValueError("pass requires every check and no failure")
        if self.status == "fail" and all(self.checks.values()):
            raise ValueError("fail requires an incomplete check")
        return self


async def _contained_project(project_name: str) -> bool:
    internal = (
        await run_command(
            ["docker", "network", "inspect", f"{project_name}_range", "--format", "{{.Internal}}"],
            timeout_seconds=20,
        )
    ).strip()
    if internal != "true":
        return False
    container_ids = (
        await run_command(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--format",
                "{{.ID}}",
            ],
            timeout_seconds=20,
        )
    ).splitlines()
    if not container_ids:
        return False
    for container_id in container_ids:
        ports = json.loads(
            await run_command(
                ["docker", "inspect", container_id, "--format", "{{json .NetworkSettings.Ports}}"],
                timeout_seconds=20,
            )
        )
        if not isinstance(ports, dict) or any(binding is not None for binding in ports.values()):
            return False
    return True


async def _project_removed(project_name: str) -> bool:
    containers = (
        await run_command(
            [
                "docker",
                "ps",
                "--all",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--format",
                "{{.ID}}",
            ],
            timeout_seconds=20,
        )
    ).strip()
    networks = (
        await run_command(
            [
                "docker",
                "network",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--format",
                "{{.ID}}",
            ],
            timeout_seconds=20,
        )
    ).strip()
    return not containers and not networks


async def check_runtime_prerequisites(
    manifest: TaskManifest, state_root: Path
) -> RuntimePrerequisiteRecord:
    """Check isolated lifecycle only; Q2 remains unevaluated without route/egress proof."""
    checks = {name: False for name in CHECKS}
    created = []
    project_names: list[str] = []
    docker_started = False
    failure_class: str | None = None
    runtime = ComposeRangeRuntime(state_root)
    try:
        if manifest.private.source_sha256 != source_pins():
            raise ValueError("source pin drift")
        build_id = await runtime.build(manifest.private.range_spec)
        runtime.state.verify_build_integrity(build_id)
        checks["source_and_build_integrity"] = True
        for _ in range(2):
            instance_id = await runtime.create_instance(build_id)
            created.append(instance_id)
            project_names.append(runtime.state.load_instance(instance_id).project_name)
            status = await runtime.start_instance(instance_id)
            docker_started = True
            if status.state != "healthy" or status.generation != 0:
                raise ValueError("instance not healthy at generation zero")
        checks["two_healthy_instances"] = True
        first = runtime.state.load_instance(created[0])
        second = runtime.state.load_instance(created[1])
        checks["instance_identity_isolation"] = (
            first.instance_id != second.instance_id
            and first.project_name != second.project_name
            and first.nonce != second.nonce
        )
        if not checks["instance_identity_isolation"]:
            raise ValueError("instance identities overlap")
        checks["internal_network_and_no_published_ports"] = all(
            [await _contained_project(name) for name in project_names]
        )
        if not checks["internal_network_and_no_published_ports"]:
            raise ValueError("network containment check failed")
        reset = await runtime.reset_instance(created[0])
        sibling = await runtime.instance_status(created[1])
        checks["reset_generation_and_sibling_isolation"] = (
            reset.state == sibling.state == "healthy"
            and reset.generation == 1
            and sibling.generation == 0
        )
        if not checks["reset_generation_and_sibling_isolation"]:
            raise ValueError("reset or sibling isolation check failed")
    except Exception as exc:
        failure_class = type(exc).__name__
    finally:
        teardown_ok = True
        for instance_id in reversed(created):
            try:
                status = await runtime.destroy_instance(instance_id)
                teardown_ok = teardown_ok and status.state == "destroyed"
            except Exception as exc:
                teardown_ok = False
                failure_class = failure_class or type(exc).__name__
        for name in project_names:
            try:
                removed = await _project_removed(name)
                teardown_ok = teardown_ok and removed
            except Exception as exc:
                teardown_ok = False
                failure_class = failure_class or type(exc).__name__
        checks["teardown"] = teardown_ok
    return RuntimePrerequisiteRecord(
        task_id=manifest.task.task_id,
        archetype_id=manifest.task.archetype_id,
        seed=manifest.task.seed,
        variant=manifest.task.variant,
        checks=checks,
        status="pass" if all(checks.values()) and failure_class is None else "fail",
        failure_class=failure_class,
        real_docker_started=docker_started,
    )
