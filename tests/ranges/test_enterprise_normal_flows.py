"""Normal authorization, uncancelled jobs, reset, and cleanup in Range B."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.specs import RangeSpec


@pytest.mark.docker
async def test_enterprise_normal_flows_and_reset(tmp_path: Path) -> None:
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
    runtime = ComposeRangeRuntime(tmp_path)
    spec = RangeSpec.model_validate(
        yaml.safe_load(
            (Path(__file__).parents[2] / "examples" / "enterprise-change-control.yaml").read_text()
        )
    )
    build_id = await runtime.build(spec)
    fixture = json.loads((runtime.state.build_dir(build_id) / "fixture.json").read_text())
    oracle = json.loads(
        (runtime.state.root / "oracles" / build_id.hex / "ground_truth.json").read_text()
    )
    roots = {item["slug"]: item for item in oracle["properties"]}
    access = next(
        item
        for item in fixture["access_requests"]
        if item["id"] == roots["B1-SOD"]["object"]["object_id"]
    )
    change = next(
        item
        for item in fixture["changes"]
        if item["id"] == roots["B3-CANCELLED-JOB"]["object"]["object_id"]
    )
    project = access["project_id"]
    separate_approver = next(
        item["user_id"]
        for item in fixture["role_assignments"]
        if item["project_id"] == project
        and item["role"] == "change_approver"
        and item["active"]
        and item["user_id"] != access["requester_id"]
    )
    wrong_actor = next(
        user["id"]
        for user in fixture["users"]
        if user["organization_id"]
        == next(p["organization_id"] for p in fixture["projects"] if p["id"] == project)
        and not any(
            role["project_id"] == project
            and role["user_id"] == user["id"]
            and role["role"] == "change_approver"
            and role["active"]
            for role in fixture["role_assignments"]
        )
    )
    operator = change["requester_id"]
    instance = await runtime.create_instance(build_id)
    startup_seconds = reset_seconds = teardown_seconds = 0.0
    try:
        startup_start = time.perf_counter()
        await runtime.start_instance(instance)
        startup_seconds = time.perf_counter() - startup_start

        async def call(method: str, path: str, actor: str | None, body=None):
            raw = await runtime.execute_enterprise_http(
                instance, method, path, body, UUID(actor) if actor else None
            )
            decoded = base64.b64decode(raw["body_b64"])
            return raw["http_status"], json.loads(decoded) if decoded else None

        access_path = f"/api/access-requests/{access['id']}"
        assert (await call("GET", access_path, None))[0] == 401
        assert (await call("POST", access_path + "/approve", wrong_actor, {}))[0] == 403
        b2 = roots["B2-REVOKED-ROLE"]
        b2_actor = b2["subject"]["identity_id"]
        b2_project = b2["object"]["workspace_id"]
        b2_admin = next(
            item["user_id"]
            for item in fixture["role_assignments"]
            if item["project_id"] == b2_project and item["role"] == "org_admin" and item["active"]
        )
        b2_path = f"/api/changes/{b2['object']['object_id']}"
        assert "approve" in (await call("GET", b2_path, b2_actor))[1]["allowed_actions"]
        revoked_status, revoked_role = await call(
            "POST",
            f"/api/projects/{b2_project}/roles/{b2_actor}/revoke",
            b2_admin,
            {"role": "change_approver"},
        )
        assert revoked_status == 200 and revoked_role["active"] is False
        # The vulnerable page still advertises the cached approval action.
        assert "approve" in (await call("GET", b2_path, b2_actor))[1]["allowed_actions"]
        approved_status, approved = await call(
            "POST", access_path + "/approve", separate_approver, {}
        )
        assert approved_status == 200 and approved["status"] == "APPLIED"
        revoked_status, revoked = await call(
            "POST", access_path + "/revoke", access["requester_id"], {}
        )
        assert revoked_status == 204 and revoked is None
        assert (await call("GET", access_path, access["requester_id"]))[1]["status"] == ("REVOKED")

        env_path = f"/api/environments/{change['environment_id']}"
        before = (await call("GET", env_path, operator))[1]["revision"]
        change_path = f"/api/changes/{change['id']}"
        assert (await call("POST", change_path + "/deploy", operator, {}))[0] == 202
        job_id = (await call("GET", change_path, operator))[1]["job_id"]
        for _ in range(4):
            status, job = await call("GET", f"/api/jobs/{job_id}", operator)
            assert status == 200
            if job["status"] == "SUCCEEDED":
                break
        assert job["status"] == "SUCCEEDED"
        assert (await call("GET", env_path, operator))[1]["revision"] == before + 1
        assert (await call("POST", change_path + "/cancel", operator, {}))[0] == 409

        reset_start = time.perf_counter()
        reset = await runtime.reset_instance(instance)
        reset_seconds = time.perf_counter() - reset_start
        assert reset.state == "healthy" and reset.generation == 1
        assert (await call("GET", access_path, access["requester_id"]))[1]["status"] == ("PENDING")
        assert (await call("GET", env_path, operator))[1]["revision"] == before
    finally:
        teardown_start = time.perf_counter()
        stopped = await runtime.destroy_instance(instance)
        teardown_seconds = time.perf_counter() - teardown_start
        assert stopped.state == "destroyed"
    if os.getenv("OFFSECGYM_TIMING") == "1":
        print(
            f"range_b_startup={startup_seconds:.2f}s "
            f"reset={reset_seconds:.2f}s teardown={teardown_seconds:.2f}s"
        )
    project_name = runtime.state.load_instance(instance).project_name
    remnants = subprocess.run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--quiet",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert not remnants.stdout.strip()


@pytest.mark.docker
async def test_b3_patch_rechecks_cancellation_without_rechecking_operator_role(
    tmp_path: Path,
) -> None:
    available = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if available.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required by CI: {available.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")
    runtime = ComposeRangeRuntime(tmp_path)
    base = RangeSpec.model_validate(
        yaml.safe_load(
            (Path(__file__).parents[2] / "examples/enterprise-change-control.yaml").read_text()
        )
    )
    build_id = await runtime.build(
        base.model_copy(update={"patched_properties": ("B3-CANCELLED-JOB",)})
    )
    fixture = json.loads((runtime.state.build_dir(build_id) / "fixture.json").read_text())
    oracle = json.loads(
        (runtime.state.root / "oracles" / build_id.hex / "ground_truth.json").read_text()
    )
    root = next(item for item in oracle["properties"] if item["slug"] == "B3-CANCELLED-JOB")
    change = next(item for item in fixture["changes"] if item["id"] == root["object"]["object_id"])
    actor = change["requester_id"]
    project = change["project_id"]
    admin = next(
        item["user_id"]
        for item in fixture["role_assignments"]
        if item["project_id"] == project and item["role"] == "org_admin" and item["active"]
    )
    instance = await runtime.create_instance(build_id)
    try:
        await runtime.start_instance(instance)

        async def call(method: str, path: str, identity: str, body=None):
            raw = await runtime.execute_enterprise_http(
                instance, method, path, body, UUID(identity)
            )
            decoded = base64.b64decode(raw["body_b64"])
            return raw["http_status"], json.loads(decoded) if decoded else None

        env_path = f"/api/environments/{change['environment_id']}"
        before = (await call("GET", env_path, actor))[1]["revision"]
        change_path = f"/api/changes/{change['id']}"
        assert (await call("POST", change_path + "/deploy", actor, {}))[0] == 202
        revoke = f"/api/projects/{project}/roles/{actor}/revoke"
        status, revoked = await call("POST", revoke, admin, {"role": "operator"})
        assert status == 200 and revoked["active"] is False
        job_id = (await call("GET", change_path, actor))[1]["job_id"]
        for _ in range(5):
            status, job = await call("GET", f"/api/jobs/{job_id}", actor)
            assert status == 200
            if job["status"] == "SUCCEEDED":
                break
        assert job["status"] == "SUCCEEDED"
        assert (await call("GET", env_path, actor))[1]["revision"] == before + 1
    finally:
        assert (await runtime.destroy_instance(instance)).state == "destroyed"
