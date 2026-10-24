"""No-model A1 Q2 runtime qualification on disposable local Docker ranges."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

from offsecgym.benchmark.manifests import make_task_manifest, source_pins
from offsecgym.benchmark.q2_isolation import (
    insert_storage_marker,
    inspect_range_attachments,
    local_canary_isolation,
    service_container,
    storage_snapshot,
)
from offsecgym.benchmark.q2_runtime_qualification import (
    MANDATORY_CHECKS,
    Q2CheckResult,
    aggregate_q2,
    evidence_digest,
)
from offsecgym.benchmark.runtime_prerequisites import (
    _project_removed,
    check_runtime_prerequisites,
)
from offsecgym.runtime.compose import ComposeRangeRuntime, run_command

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_TEST = ROOT / "tests/ranges/test_benchmark_t11_gateway_integrity.py"
PUBLIC_REPORT = ROOT / "docs/benchmark/t11b-q2-runtime-qualification.json"


def _check(payload: object, *, passed: bool, reason: str) -> Q2CheckResult:
    return Q2CheckResult(
        status="pass" if passed else "fail",
        reason_code="verified" if passed else reason,
        evidence_sha256=evidence_digest(payload),
    )


def _private_write(path: Path, payload: object) -> None:
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def _source_commit() -> str:
    bound_paths = (
        "src/offsecgym/benchmark/q2_isolation.py",
        "src/offsecgym/benchmark/q2_runtime_qualification.py",
        "research_ops/benchmark_t11b_q2_qualify.py",
        "tests/ranges/test_benchmark_t11_gateway_integrity.py",
        "tests/ranges/test_benchmark_t11b_network_isolation.py",
        "tests/ranges/test_benchmark_t11b_state_isolation.py",
        "tests/unit/test_benchmark_t11b_q2.py",
    )
    for path in bound_paths:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", path],
            cwd=ROOT,
            capture_output=True,
            timeout=10,
            check=True,
        )
    subprocess.run(
        ["git", "diff", "HEAD", "--quiet", "--", *bound_paths],
        cwd=ROOT,
        capture_output=True,
        timeout=10,
        check=True,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10
    ).strip()


def _gateway_test() -> dict[str, object]:
    test_hash = hashlib.sha256(GATEWAY_TEST.read_bytes()).hexdigest()
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(GATEWAY_TEST)],
        cwd=ROOT,
        env={**os.environ, "OFFSECGYM_REQUIRE_DOCKER": "1"},
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    return {
        "test_sha256": test_hash,
        "exit_code": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "stdout_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(process.stderr.encode()).hexdigest(),
        "passed": process.returncode == 0,
    }


async def _run(
    private_root: Path, task_id: str, source_commit: str, source_hashes: dict[str, str]
) -> dict[str, object]:
    checks: dict[str, Q2CheckResult] = {}
    evidence: dict[str, object] = {
        "task_id": task_id,
        "source_commit": source_commit,
        "source_sha256": source_hashes,
        "gateway_test_sha256": hashlib.sha256(GATEWAY_TEST.read_bytes()).hexdigest(),
    }
    manifest = make_task_manifest("A1", task_mode="scoped_audit", variant="vulnerable", seed=42)
    if manifest.task.task_id != task_id or manifest.private.source_sha256 != source_hashes:
        raise ValueError("source_pin_mismatch")
    try:
        docker_version = await run_command(
            ["docker", "info", "--format", "{{.ServerVersion}}"], timeout_seconds=10
        )
    except Exception:
        evidence["docker_available"] = False
        checks["healthy_runtime"] = Q2CheckResult(
            status="unknown", reason_code="docker_unavailable"
        )
        return _finalize(task_id, source_commit, source_hashes, checks, evidence)
    evidence["docker_available"] = True
    evidence["docker_server_version"] = docker_version.strip()

    # Existing PR #75 lifecycle proof is rerun against this exact task/source.
    prereq = await check_runtime_prerequisites(manifest, private_root / "prerequisites")
    evidence["prerequisites"] = prereq.model_dump(mode="json")
    prereq_map = {
        "source_build_integrity": "source_and_build_integrity",
        "healthy_runtime": "two_healthy_instances",
        "instance_identity_isolation": "instance_identity_isolation",
        "internal_network_configuration": "internal_network_and_no_published_ports",
        "no_published_ports": "internal_network_and_no_published_ports",
    }
    for name, existing in prereq_map.items():
        checks[name] = _check(
            {"task_id": task_id, "prerequisite": existing, "record": evidence["prerequisites"]},
            passed=prereq.checks[existing],
            reason="prerequisite_failed",
        )
    if prereq.status != "pass":
        checks["teardown_integrity"] = _check(
            evidence["prerequisites"], passed=prereq.checks["teardown"], reason="cleanup_failure"
        )
        return _finalize(task_id, source_commit, source_hashes, checks, evidence)

    runtime = ComposeRangeRuntime(private_root / "isolation")
    created: list[UUID] = []
    projects: list[str] = []
    teardown_ok = True
    try:
        build_id = await runtime.build(manifest.private.range_spec)
        build = runtime.state.verify_build_integrity(build_id)
        for _ in range(2):
            instance_id = await runtime.create_instance(build_id)
            created.append(instance_id)
            projects.append(runtime.state.load_instance(instance_id).project_name)
            status = await runtime.start_instance(instance_id)
            if status.state != "healthy" or status.generation != 0:
                raise ValueError("runtime_start_failure")
        first = runtime.state.load_instance(created[0])
        second = runtime.state.load_instance(created[1])
        if first.nonce == second.nonce or first.project_name == second.project_name:
            raise ValueError("instance_identity_isolation")

        inventory = [
            await inspect_range_attachments(project, instance_id)
            for project, instance_id in zip(projects, created, strict=True)
        ]
        evidence["network_inventory_sha256"] = inventory
        checks["network_attachment_integrity"] = _check(
            inventory, passed=True, reason="network_attachment_mismatch"
        )
        target_a = await service_container(first.project_name, "saas", created[0])
        target_b = await service_container(second.project_name, "saas", created[1])
        canary = await local_canary_isolation(target_a, build.image_name)
        evidence["canary"] = canary
        canary_ok = all(
            canary.get(key) is True
            for key in (
                "control_connected",
                "probe_executed",
                "range_to_canary_blocked",
                "connection_count_unchanged",
                "attachment_isolation",
                "canary_network_internal",
                "cleanup_ok",
            )
        )
        checks["active_local_canary_isolation"] = _check(
            canary, passed=canary_ok, reason="active_canary_isolation_failed"
        )
        initial_a = await storage_snapshot(target_a)
        initial_b = await storage_snapshot(target_b)
        await insert_storage_marker(target_a)
        modified_a = await storage_snapshot(target_a)
        unchanged_b = await storage_snapshot(target_b)
        snapshots = {
            "instance_a_initial_state": initial_a,
            "instance_b_initial_state": initial_b,
            "instance_a_modified_state": modified_a,
            "instance_b_unmodified_state": unchanged_b,
        }
        business = initial_a["business_sha256"]
        storage_ok = (
            initial_a == initial_b
            and initial_a["marker_present"] is False
            and modified_a["marker_present"] is True
            and unchanged_b == initial_b
            and modified_a["business_sha256"] == business
        )
        reset = await runtime.reset_instance(created[0])
        sibling = await runtime.instance_status(created[1])
        reset_target = await service_container(first.project_name, "saas", created[0])
        after_reset_a = await storage_snapshot(reset_target)
        after_reset_b = await storage_snapshot(target_b)
        snapshots["instance_a_after_reset"] = after_reset_a
        snapshots["instance_b_after_a_reset"] = after_reset_b
        evidence["storage_snapshots"] = snapshots
        checks["trusted_application_storage_isolation"] = _check(
            snapshots, passed=storage_ok, reason="application_state_mismatch"
        )
        reset_ok = (
            reset.state == sibling.state == "healthy"
            and reset.generation == 1
            and sibling.generation == 0
            and after_reset_a == initial_a
            and after_reset_b == initial_b
        )
        checks["reset_state_integrity"] = _check(
            {
                "snapshots": snapshots,
                "reset_generation": reset.generation,
                "sibling_generation": sibling.generation,
            },
            passed=reset_ok,
            reason="reset_state_mismatch",
        )
    except Exception as exc:
        evidence["isolation_failure_class"] = type(exc).__name__
        if "network_attachment_integrity" not in checks:
            checks["network_attachment_integrity"] = Q2CheckResult(
                status="unknown", reason_code="network_inspection_incomplete"
            )
        if "active_local_canary_isolation" not in checks:
            checks["active_local_canary_isolation"] = Q2CheckResult(
                status="unknown", reason_code="active_probe_incomplete"
            )
        if "trusted_application_storage_isolation" not in checks:
            checks["trusted_application_storage_isolation"] = Q2CheckResult(
                status="unknown", reason_code="storage_probe_incomplete"
            )
        if "reset_state_integrity" not in checks:
            checks["reset_state_integrity"] = Q2CheckResult(
                status="unknown", reason_code="reset_probe_incomplete"
            )
    finally:
        for instance_id in reversed(created):
            try:
                teardown_ok &= (await runtime.destroy_instance(instance_id)).state == "destroyed"
            except Exception:
                teardown_ok = False
        for project in projects:
            try:
                teardown_ok &= await _project_removed(project)
            except Exception:
                teardown_ok = False
        evidence["range_teardown_ok"] = teardown_ok
        checks["teardown_integrity"] = _check(
            {
                "range_teardown_ok": teardown_ok,
                "canary_cleanup_ok": evidence.get("canary", {}).get("cleanup_ok"),
            },
            passed=teardown_ok and evidence.get("canary", {}).get("cleanup_ok") is True,
            reason="cleanup_failure",
        )

    # PR #76 test is source-bound and rerun; all its named assertions must pass.
    gateway = _gateway_test()
    evidence["gateway"] = gateway
    for name in (
        "permitted_gateway_action",
        "forbidden_route_and_destination",
        "identity_run_generation_binding",
        "budget_and_duplicate_enforcement",
        "event_and_artifact_integrity",
    ):
        checks[name] = _check(
            {"task_id": task_id, "source_commit": source_commit, "test": gateway, "check": name},
            passed=gateway["passed"] is True,
            reason="gateway_policy_failure",
        )
    return _finalize(task_id, source_commit, source_hashes, checks, evidence)


def _finalize(
    task_id: str,
    source_commit: str,
    source_hashes: dict[str, str],
    checks: dict[str, Q2CheckResult],
    evidence: dict[str, object],
) -> dict[str, object]:
    record = aggregate_q2(
        task_id=task_id,
        source_commit=source_commit,
        source_sha256=source_hashes,
        checks=checks,
        real_docker_used=evidence.get("docker_available") is True,
        real_gateway_used=evidence.get("gateway", {}).get("passed") is True,
        local_canary_used=evidence.get("canary", {}).get("control_connected") is True,
    )
    return {"record": record.model_dump(mode="json"), "evidence": evidence}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archetype", choices=("A1",), required=True)
    parser.add_argument("--seed", type=int, choices=(42,), required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--output-private", type=Path)
    args = parser.parse_args()
    manifest = make_task_manifest("A1", task_mode="scoped_audit", variant="vulnerable", seed=42)
    pins = source_pins()
    if pins != manifest.private.source_sha256:
        raise SystemExit("source_pin_mismatch")
    commit = _source_commit()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "milestone": "T1.1b",
                    "task_id": manifest.task.task_id,
                    "source_commit": commit,
                    "mandatory_checks": MANDATORY_CHECKS,
                    "docker_started": False,
                    "provider_calls": 0,
                    "q2_status": "not_evaluated",
                    "owner_label": "org.offsecgym.t11b_owner",
                    "max_instances": 2,
                    "max_canary_containers": 2,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return
    if args.output_private is None:
        parser.error("--execute requires --output-private")
    base = args.output_private.resolve()
    if base.is_symlink() or not str(base).startswith(str((ROOT / ".offsecgym").resolve()) + os.sep):
        parser.error("private output must be under this checkout's .offsecgym directory")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_root = base / uuid4().hex
    run_root.mkdir(mode=0o700)
    result = asyncio.run(_run(run_root, manifest.task.task_id, commit, pins))
    _private_write(run_root / "evidence.json", result["evidence"])
    _private_write(run_root / "record.json", result["record"])
    report = result["record"]
    canonical = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    if canonical != (json.dumps(json.loads(canonical), sort_keys=True, indent=2) + "\n").encode():
        raise ValueError("public report reconstruction mismatch")
    PUBLIC_REPORT.write_bytes(canonical)
    print(
        json.dumps(
            {
                "q2_status": report["q2_status"],
                "blocking_checks": report["q2_blocking_reasons"],
                "public_report_sha256": hashlib.sha256(canonical).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
