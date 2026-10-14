"""Deterministic no-model qualification of prospective M6.7 infrastructure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import yaml

from offsecgym.experiment.m67 import M67Arm
from offsecgym.reporting.provenance import canonical_route
from offsecgym.solver.monolithic import model_tools

ROOT = Path(__file__).resolve().parents[1]
FAMILY = "enterprise_change_control_v1"
BASELINE_SCHEMA_SHA256 = {
    "enterprise_change_control_v1": (
        "65aaf592c8e715257676f0fe3ee11843ed5d052123506f97c0e4f5bcfe4b1a44"
    ),
    "saas": "a68a1b0863157473bfd1335ca681a6cc4c701561b186532d9c1dfa185d7a9a06",
}
CONFIGS = {
    "A0B0": "m67-baseline.yaml",
    "A1B0": "m67-reporting.yaml",
    "A0B1": "m67-commitment.yaml",
    "A1B1": "m67-reporting-commitment.yaml",
}
SOURCE_PATHS = (
    "src/offsecgym/reporting/provenance.py",
    "src/offsecgym/solver/experiment_state.py",
    "src/offsecgym/solver/monolithic.py",
    "src/offsecgym/schemas/events.py",
    "src/offsecgym/experiment/m67.py",
    "src/offsecgym/research/m67_repeat_analysis.py",
    "src/offsecgym/research/m67_stage_analysis.py",
    "research_ops/m67_qualify.py",
    "docs/diagnostics/m67-stage-interventions-design.md",
)
TESTS = (
    "tests/unit/test_m67_provenance.py",
    "tests/unit/test_m67_experiment.py",
    "tests/unit/test_m67_repeat_analysis.py",
    "tests/unit/test_m67_stage_analysis.py",
    "tests/unit/test_m67_agent_integration.py",
)
COMPOSE_TEST = "tests/ranges/test_m67_compose_qualification.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualify() -> dict[str, object]:
    arms: dict[str, M67Arm] = {}
    config_hashes = {}
    for expected, filename in CONFIGS.items():
        path = ROOT / "experiments" / "configs" / filename
        arm = M67Arm.model_validate(yaml.safe_load(path.read_text()))
        if arm.name != expected:
            raise ValueError(f"factorial config mismatch: {filename}")
        arms[expected] = arm
        config_hashes[filename] = _sha(path)
    schemas = {
        name: model_tools(
            family=FAMILY,
            structured=True,
            provenance_reporting=arm.provenance_reporting,
            experiment_commitment=arm.experiment_commitment,
        )
        for name, arm in arms.items()
    }
    names = {name: {item["name"] for item in tools} for name, tools in schemas.items()}
    reporting = {"list_candidate_evidence", "preflight_finding"}
    commitment = {
        "start_experiment",
        "get_active_experiment",
        "update_experiment",
        "complete_experiment",
        "abandon_experiment",
    }
    if not (
        names["A1B0"] - names["A0B0"] == reporting
        and names["A0B1"] - names["A0B0"] == commitment
        and names["A1B1"] == names["A0B0"] | reporting | commitment
    ):
        raise ValueError("factorial tool separation failed")
    for family, expected_hash in BASELINE_SCHEMA_SHA256.items():
        if arms["A0B0"].schema_sha256(family=family, structured=True) != expected_hash:
            raise ValueError(f"baseline tool schema changed for {family}")
    probe = "/api/changes/8e5f3927-d0dc-48f6-b34b-8b730b2c39ca/approve"
    if canonical_route(FAMILY, "POST", probe) != "POST /api/changes/{id}/approve":
        raise ValueError("public route canonicalization failed")
    if canonical_route(FAMILY, "POST", "/api/changes/private/approve") is not None:
        raise ValueError("malformed route was accepted")
    test = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *TESTS],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if test.returncode != 0:
        raise RuntimeError(f"no-model tests failed:\n{test.stdout[-3000:]}\n{test.stderr[-1000:]}")
    output = test.stdout.strip().splitlines()[-1]
    if "passed" not in output:
        raise RuntimeError("no-model test summary missing")
    deterministic_result = output.split(" in ", 1)[0]
    compose_command = [sys.executable, "-m", "pytest", "-q", COMPOSE_TEST]
    with tempfile.TemporaryDirectory(prefix="m67-compose-qualification-") as receipt_dir:
        env = os.environ.copy()
        env["OFFSECGYM_REQUIRE_DOCKER"] = "1"
        env["OFFSECGYM_M67_QUALIFICATION_RECEIPT"] = receipt_dir
        compose = subprocess.run(
            compose_command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if compose.returncode:
            raise RuntimeError(
                f"real Compose qualification failed:\n{compose.stdout[-3000:]}"
                f"\n{compose.stderr[-1000:]}"
            )
        compose_summary = compose.stdout.strip().splitlines()[-1]
        if "2 passed" not in compose_summary:
            raise RuntimeError("real Compose qualification did not pass both ranges")
        receipts = [
            json.loads(path.read_text()) for path in sorted(Path(receipt_dir).glob("*.json"))
        ]
        if {item["family"] for item in receipts} != {"saas", FAMILY}:
            raise RuntimeError("real Compose qualification receipts incomplete")
    real_cases = [
        {
            "family": item["family"],
            "case": case,
            "passed": True,
            "artifact_sha256": item["artifact_sha256"],
        }
        for item in receipts
        for case in item["cases"]
    ]
    source_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    return {
        "milestone": "M6.7",
        "status": "engineering_design_unapproved_unrun",
        "provider_calls": 0,
        "live_manifest_frozen": False,
        "real_compose_cases": real_cases,
        "compose_test_command": compose_command,
        "compose_test_result": compose_summary.split(" in ", 1)[0],
        "source_head_at_qualification": source_head,
        "factorial_arm_tool_schema_sha256": {
            name: arm.schema_sha256(family=FAMILY, structured=True) for name, arm in arms.items()
        },
        "factorial_arm_tool_names": {name: sorted(value) for name, value in names.items()},
        "pre_m67_baseline_schema_sha256": BASELINE_SCHEMA_SHA256,
        "config_sha256": config_hashes,
        "source_sha256": {name: _sha(ROOT / name) for name in SOURCE_PATHS},
        "test_sha256": {name: _sha(ROOT / name) for name in (*TESTS, COMPOSE_TEST)},
        "python_version": sys.version.split()[0],
        "no_model_test_command": [sys.executable, "-m", "pytest", "-q", *TESTS],
        "no_model_test_result": deterministic_result,
        "route_probe": {
            "concrete_path": probe,
            "canonical_shape": "POST /api/changes/{id}/approve",
            "malformed_rejected": True,
        },
        "synthetic_uuid": str(UUID("8e5f3927-d0dc-48f6-b34b-8b730b2c39ca")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "diagnostics" / "m67-no-model-qualification.json",
    )
    args = parser.parse_args()
    result = qualify()
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(f"M6.7 no-model qualification passed: {result['no_model_test_result']}")


if __name__ == "__main__":
    main()
