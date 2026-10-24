"""Trusted SQLite marker verifies disposable sibling and reset storage isolation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.benchmark.q2_isolation import (
    insert_storage_marker,
    service_container,
    storage_snapshot,
)
from offsecgym.benchmark.runtime_prerequisites import _project_removed
from offsecgym.runtime.compose import ComposeRangeRuntime


@pytest.mark.docker
async def test_trusted_application_storage_and_reset_isolation(tmp_path: Path) -> None:
    available = (
        subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=10,
            check=False,
        ).returncode
        == 0
    )
    if not available:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail("Docker daemon required")
        pytest.skip("Docker daemon unavailable")
    manifest = make_task_manifest("A1", task_mode="scoped_audit", variant="vulnerable", seed=42)
    runtime = ComposeRangeRuntime(tmp_path)
    build_id = await runtime.build(manifest.private.range_spec)
    instances = [await runtime.create_instance(build_id) for _ in range(2)]
    projects = [runtime.state.load_instance(instance_id).project_name for instance_id in instances]
    assert projects[0] != projects[1]
    assert (
        runtime.state.load_instance(instances[0]).nonce
        != runtime.state.load_instance(instances[1]).nonce
    )
    try:
        for instance_id in instances:
            assert (await runtime.start_instance(instance_id)).state == "healthy"
        targets = [
            await service_container(project, "saas", instance_id)
            for project, instance_id in zip(projects, instances, strict=True)
        ]
        first = await storage_snapshot(targets[0])
        second = await storage_snapshot(targets[1])
        assert first == second and first["marker_present"] is False
        await insert_storage_marker(targets[0])
        modified = await storage_snapshot(targets[0])
        assert modified["marker_present"] is True
        assert modified["business_sha256"] == first["business_sha256"]
        assert await storage_snapshot(targets[1]) == second
        reset = await runtime.reset_instance(instances[0])
        sibling = await runtime.instance_status(instances[1])
        assert reset.state == sibling.state == "healthy"
        assert reset.generation == 1 and sibling.generation == 0
        reset_target = await service_container(projects[0], "saas", instances[0])
        assert await storage_snapshot(reset_target) == first
        assert await storage_snapshot(targets[1]) == second
    finally:
        for instance_id in reversed(instances):
            assert (await runtime.destroy_instance(instance_id)).state == "destroyed"
        for project in projects:
            assert await _project_removed(project)
