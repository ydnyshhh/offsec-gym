"""Detached R0 A1 protocol inspection. Model execution is not connected."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from offsecgym.benchmark.tasks import canonical_sha256
from offsecgym.research.pilot.protocol import admission_baseline, dry_run, load_protocol
from offsecgym.research.pilot.reporting import write_offline_reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--analyze-existing", action="store_true")
    mode.add_argument("--execute-models", action="store_true")
    args = parser.parse_args()
    if args.execute_models:
        parser.error("model execution is unavailable: scoped A1 controller parity is not qualified")
    protocol = load_protocol(args.protocol)
    if args.analyze_existing:
        if args.input is None or args.output is None:
            parser.error("--analyze-existing requires --input and --output")
        admission_baseline(Path("docs/benchmark/t11f-a1-admission.json"))
        summary = write_offline_reports(
            args.input,
            args.output,
            expected_budget_hash=canonical_sha256(protocol.budget),
        )
        print(
            json.dumps(
                {"status": summary["status"], "actual_model_runs": summary["actual_model_runs"]}
            )
        )
        return 0
    result = dry_run(protocol, Path("docs/benchmark/t11f-a1-admission.json"))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
