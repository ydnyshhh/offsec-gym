"""Create an explicit, source-bound T1.2 static matrix without runtime execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from offsecgym.benchmark.a1_runtime import require_private_root
from offsecgym.benchmark.t12_static_audit import (
    audit_static_cells,
    dry_run,
    qualification_source_pins,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "docs/benchmark/t12-static-prequalification.json"
A1_BASELINE = ROOT / "docs/benchmark/t11f-a1-admission.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute-static", action="store_true")
    parser.add_argument("--output-private", type=Path)
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps(dry_run(ROOT), sort_keys=True))
        return
    if args.output_private is None:
        parser.error("--execute-static requires --output-private")
    private_root = require_private_root(args.output_private, ROOT)
    before = A1_BASELINE.read_bytes()
    report = audit_static_cells(
        private_root,
        a1_admission_path=A1_BASELINE,
        qualification_source_sha256=qualification_source_pins(ROOT),
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
