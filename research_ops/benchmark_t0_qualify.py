"""Generate static T0 registry and qualification artifacts without model calls."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from offsecgym.benchmark.qualification import (
    default_registry_manifests,
    qualification_aggregate,
    qualify_static,
)
from offsecgym.benchmark.tasks import canonical_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate schemas and source pins only"
    )
    parser.add_argument("--qualify", action="store_true", help="Run disposable compiler checks")
    parser.add_argument("--output", type=Path, help="Public aggregate output directory")
    args = parser.parse_args()
    if args.dry_run == args.qualify:
        parser.error("choose exactly one of --dry-run or --qualify")
    manifests = default_registry_manifests()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "registered_archetypes": len(manifests),
                    "schema_valid": True,
                    "docker_started": False,
                    "provider_calls": 0,
                },
                sort_keys=True,
            )
        )
        return
    with tempfile.TemporaryDirectory(prefix="offsecgym-t0-") as temporary:
        records = tuple(qualify_static(item, Path(temporary)) for item in manifests)
    aggregate = qualification_aggregate(records)
    registry = {
        "schema_version": "1",
        "benchmark_version": "0.1.0-draft",
        "archetypes": [
            {
                "archetype_id": item.task.archetype_id,
                "range_family": item.task.range_family,
                "scenario": item.task.scenario,
                "root_slug": item.private.expected_root_slug,
                "task_mode": item.task.task_mode,
                "task_id": item.task.task_id,
                "readiness": row.readiness,
            }
            for item, row in zip(manifests, records, strict=True)
        ],
    }
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        for name, payload in (
            ("t0-task-registry.json", registry),
            ("t0-qualification.json", aggregate),
        ):
            (args.output / name).write_bytes(canonical_bytes(payload) + b"\n")
    print(
        json.dumps(
            {
                "registered_archetypes": len(records),
                "readiness": {r.archetype_id: r.readiness for r in records},
                "aggregate_sha256": aggregate["record_set_sha256"],
                "docker_started": False,
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
