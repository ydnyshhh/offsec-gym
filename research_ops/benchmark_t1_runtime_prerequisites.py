"""Run one benign T1 Docker lifecycle prerequisite check; do not execute task actions."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.benchmark.registry import ARCHETYPES
from offsecgym.benchmark.runtime_prerequisites import check_runtime_prerequisites
from offsecgym.benchmark.tasks import canonical_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archetype", required=True, choices=sorted(ARCHETYPES))
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--variant", required=True, choices=("vulnerable", "patched"))
    parser.add_argument("--output", type=Path, help="Optional sanitized JSON evidence path")
    args = parser.parse_args()
    if args.seed < 0:
        parser.error("seed must be nonnegative")

    manifest = make_task_manifest(
        args.archetype, task_mode="scoped_audit", variant=args.variant, seed=args.seed
    )
    with tempfile.TemporaryDirectory(prefix="offsecgym-t1-runtime-") as temporary:
        record = asyncio.run(check_runtime_prerequisites(manifest, Path(temporary)))
    payload = record.model_dump(mode="json")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_bytes(payload) + b"\n")
    print(json.dumps(payload, sort_keys=True))
    if record.status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
