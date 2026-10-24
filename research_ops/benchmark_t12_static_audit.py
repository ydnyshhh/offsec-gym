"""Create an explicit, source-bound T1.2 static matrix without runtime execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from offsecgym.benchmark.a1_runtime import require_private_root
from offsecgym.benchmark.manifests import source_pins
from offsecgym.benchmark.t12_static_audit import (
    ARCHETYPES,
    POLICY_VERSION,
    SEEDS,
    VARIANTS,
    audit_static_cells,
    execution_matrix,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "docs/benchmark/t12-static-prequalification.json"
A1_BASELINE = ROOT / "docs/benchmark/t11f-a1-admission.json"
BOUND_FILES = (
    "src/offsecgym/benchmark/t12_static_audit.py",
    "research_ops/benchmark_t12_static_audit.py",
    "src/offsecgym/benchmark/qualification.py",
    "src/offsecgym/benchmark/manifests.py",
    "src/offsecgym/benchmark/a1_admission.py",
)


def qualification_source_pins() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in BOUND_FILES}


def dry_run() -> dict[str, object]:
    return {
        "qualification_policy": POLICY_VERSION,
        "archetypes": ARCHETYPES,
        "seeds": SEEDS,
        "variants": VARIANTS,
        "cells": [
            {"archetype_id": archetype, "seed": seed, "variant": variant, "task_id": task_id}
            for archetype, seed, variant, task_id in execution_matrix()
        ],
        "required_for_full_qualification": ("Q2", "Q3", "Q4", "Q5", "Q6"),
        "source_sha256": source_pins(),
        "qualification_source_sha256": qualification_source_pins(),
        "report_destination": str(PUBLIC.relative_to(ROOT)),
        "docker_starts": 0,
        "gateway_actions": 0,
        "provider_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute-static", action="store_true")
    parser.add_argument("--output-private", type=Path)
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps(dry_run(), sort_keys=True))
        return
    if args.output_private is None:
        parser.error("--execute-static requires --output-private")
    private_root = require_private_root(args.output_private, ROOT)
    before = A1_BASELINE.read_bytes()
    report = audit_static_cells(
        private_root,
        a1_admission_path=A1_BASELINE,
        qualification_source_sha256=qualification_source_pins(),
    )
    if A1_BASELINE.read_bytes() != before:
        raise ValueError("A1 baseline changed during T1.2 static audit")
    PUBLIC.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                "cell_count": len(report["cells"]),
                "newly_qualified_archetypes": report["newly_qualified_archetypes"],
                "normalized_sha256": report["normalized_sha256"],
                "provider_calls": report["provider_calls"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
