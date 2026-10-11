"""Create a no-model M6.6.1 fixture/build and Compose qualification report once."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import yaml

from offsecgym.research.m661_checkpoints import REGISTRY, VERSION
from offsecgym.research.m661_seed_exclusions import (
    REGISTRY_PATH,
    load_exclusion_registry,
)
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.enterprise import fixture_for_seed
from offsecgym.schemas.specs import ExperimentSpec


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compose_result(junit_path: Path) -> dict[str, object]:
    root = ET.fromstring(junit_path.read_bytes())
    cases = root.findall(".//testcase")
    names = [item.attrib.get("name", "") for item in cases]
    if (
        len(cases) != 5
        or len(set(names)) != 5
        or any(
            not name.startswith("test_real_checkpoint_is_audited_isolated_and_patch_correct")
            for name in names
        )
        or any(
            item.find("failure") is not None
            or item.find("error") is not None
            or item.find("skipped") is not None
            for item in cases
        )
    ):
        raise ValueError("five required no-model Compose qualification cases did not pass")
    return {"status": "passed", "cases": names, "junit_sha256": _sha(junit_path), "model_calls": 0}


def simulated_result(junit_path: Path) -> dict[str, object]:
    root = ET.fromstring(junit_path.read_bytes())
    cases = root.findall(".//testcase")
    names = [item.attrib.get("name", "") for item in cases]
    b2 = [
        name
        for name in names
        if name.startswith("test_b2_checkpoints_preserve_role_and_cache_semantics")
    ]
    b3 = [name for name in names if name.startswith("test_b3_checkpoints_leave_worker_window_open")]
    if (
        len(b2) != 144
        or len(b3) != 144
        or len(set(b2 + b3)) != 288
        or any(
            item.find("failure") is not None
            or item.find("error") is not None
            or item.find("skipped") is not None
            for item in cases
        )
    ):
        raise ValueError("288 seed/checkpoint/variant simulated qualification cases did not pass")
    return {
        "status": "passed",
        "cases": 288,
        "seeds": 24,
        "variants_per_checkpoint": 2,
        "junit_sha256": _sha(junit_path),
        "model_calls": 0,
    }


def qualify(
    repository_root: Path, state_dir: Path, compose_junit: Path, simulated_junit: Path
) -> dict[str, object]:
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository_root, text=True
    ).strip()
    excluded, registry_sha = load_exclusion_registry(repository_root)
    registry = json.loads((repository_root / REGISTRY_PATH).read_bytes())
    seeds = registry["qualification_seeds"]
    if len(seeds) != 24 or not set(seeds) <= excluded:
        raise ValueError("qualification seed snapshot differs")
    config = yaml.safe_load(
        (
            repository_root / "experiments/configs/m661-range-b-checkpoint-qualification.yaml"
        ).read_text()
    )
    runtime = ComposeRangeRuntime(state_dir)
    rows = []
    for seed in seeds:
        raw = json.loads(json.dumps(config))
        raw["seed"] = raw["range"]["seed"] = seed
        spec = ExperimentSpec.model_validate(raw)
        fixture, anchors = fixture_for_seed(seed)
        if (fixture, anchors) != fixture_for_seed(seed):
            raise ValueError("fixture is not deterministic")
        builds = []
        for patches in ((), ("B2-REVOKED-ROLE",), ("B3-CANCELLED-JOB",)):
            variant = spec.range.model_copy(update={"patched_properties": patches})
            build_id = runtime.enterprise_compiler.build(variant).build_id
            build = runtime.state.verify_build_integrity(build_id)
            builds.append(build)
        if len({item.pair_id for item in builds}) != 1:
            raise ValueError("vulnerable/patched pair binding differs")
        fixture_hashes = {
            _sha(runtime.state.build_dir(item.build_id) / "fixture.json") for item in builds
        }
        if len(fixture_hashes) != 1:
            raise ValueError("vulnerable/patched fixture differs")
        checkpoint_fingerprints = {
            item.checkpoint_id: hashlib.sha256(
                json.dumps(
                    {
                        "seed": seed,
                        "checkpoint": item.checkpoint_id,
                        "version": VERSION,
                        "fixture_sha256": next(iter(fixture_hashes)),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            for item in REGISTRY.all()
        }
        if len(set(checkpoint_fingerprints.values())) != len(REGISTRY.all()):
            raise ValueError("checkpoint identity collision")
        rows.append(
            {
                "seed": seed,
                "fixture_sha256": next(iter(fixture_hashes)),
                "pair_id": str(builds[0].pair_id),
                "build_ids": [str(item.build_id) for item in builds],
                "checkpoint_fingerprints": checkpoint_fingerprints,
            }
        )
    return {
        "protocol": VERSION,
        "source_commit": source_commit,
        "created_at": datetime.now(UTC).isoformat(),
        "registry_sha256": registry_sha,
        "qualification_seed_count": len(seeds),
        "compiled_build_count": len(rows) * 3,
        "checkpoint_count": len(REGISTRY.all()),
        "fixture_and_patch_pair_checks": "passed",
        "compose": compose_result(compose_junit),
        "simulated_state_machine": simulated_result(simulated_junit),
        "model_calls": 0,
        "paid_model_calls": 0,
        "future_study_seeds_selected": False,
        "source_file_sha256": {
            name: _sha(repository_root / name)
            for name in (
                "src/offsecgym/research/m661_checkpoints.py",
                "src/offsecgym/research/m661_checkpoint_analysis.py",
                "src/offsecgym/experiment/critical_checkpoint.py",
            )
        },
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--compose-junit", type=Path, required=True)
    parser.add_argument("--simulated-junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output = (root / args.output).resolve()
    expected = root / "docs/diagnostics/m661-checkpoint-qualification.json"
    if output != expected:
        raise ValueError("qualification output path differs from versioned report")
    report = qualify(
        root, args.state_dir.resolve(), args.compose_junit.resolve(), args.simulated_junit.resolve()
    )
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "report_sha256": _sha(output),
                "qualification_seed_count": report["qualification_seed_count"],
                "compose_cases": len(report["compose"]["cases"]),
                "paid_model_calls": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
