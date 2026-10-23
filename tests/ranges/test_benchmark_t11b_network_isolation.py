"""Active, local-only A1 Docker network isolation; no public destination."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.benchmark.q2_isolation import (
    inspect_range_attachments,
    local_canary_isolation,
    service_container,
)
from offsecgym.benchmark.runtime_prerequisites import _project_removed
from offsecgym.runtime.compose import ComposeRangeRuntime


@pytest.mark.docker
async def test_active_local_canary_isolation(tmp_path: Path) -> None:
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
    image_name = runtime.state.verify_build_integrity(build_id).image_name
    instance_id = await runtime.create_instance(build_id)
    project = runtime.state.load_instance(instance_id).project_name
    try:
        assert (await runtime.start_instance(instance_id)).state == "healthy"
        assert len(await inspect_range_attachments(project, instance_id)) == 64
        target = await service_container(project, "saas", instance_id)
        result = await local_canary_isolation(target, image_name)
        assert all(result.values())
    finally:
        assert (await runtime.destroy_instance(instance_id)).state == "destroyed"
        assert await _project_removed(project)
