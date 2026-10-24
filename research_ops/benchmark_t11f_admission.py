"""Derive A1 Q1-Q9 admission from frozen six-cell qualification evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from offsecgym.benchmark.a1_admission import SEEDS, VARIANTS, admit_a1
from offsecgym.benchmark.a1_runtime import (
    private_write,
    qualification_source_pins,
    require_private_root,
)
from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.benchmark.qualification import qualify_static

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs/benchmark"


def _read(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    return value if isinstance(value, dict) else None


def execute(private_root: Path) -> dict[str, object]:
    static = {}
    q2 = {}
    reference = {}
    for seed in SEEDS:
        for variant in VARIANTS:
            key = (seed, variant)
            manifest = make_task_manifest(
                "A1", task_mode="scoped_audit", variant=variant, seed=seed
            )
            static[key] = qualify_static(
                manifest, private_root / "static" / f"{seed}-{variant}"
            ).model_dump(mode="json")
            q2[key] = _read(DOCS / f"t11f-a1-q2-{seed}-{variant}.json")
            reference[key] = _read(DOCS / f"t11f-a1-reference-{seed}-{variant}.json")
    conformance = _read(DOCS / "t11d-validator-conformance.json") or {}
    result = admit_a1(
        static=static,
        q2=q2,
        reference=reference,
        conformance=conformance,
        qualification_pins=qualification_source_pins(ROOT),
    )
    canonical = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if canonical != json.dumps(json.loads(canonical), sort_keys=True, indent=2) + "\n":
        raise ValueError("admission report did not regenerate byte-identically")
    (DOCS / "t11f-a1-admission.json").write_text(canonical)
    private_write(
        private_root / "admission-private.json",
        {
            "source_files": {
                "q2": {str(key): bool(value) for key, value in q2.items()},
                "reference": {str(key): bool(value) for key, value in reference.items()},
            },
            "normalized_sha256": result["normalized_sha256"],
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archetype", choices=("A1",), required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--output-private", type=Path)
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({"seeds": SEEDS, "variants": VARIANTS, "provider_calls": 0}))
        return
    if args.output_private is None:
        parser.error("--execute requires --output-private")
    private_root = require_private_root(args.output_private, ROOT)
    result = execute(private_root)
    print(
        json.dumps(
            {key: result[key] for key in ("q9_readiness", "admitted", "normalized_sha256")},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
