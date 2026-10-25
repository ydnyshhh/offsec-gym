"""Analyze exported authoritative model-run traces entirely offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from offsecgym.research.pilot.reporting import write_offline_reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = write_offline_reports(args.input, args.output)
    print(
        json.dumps({"status": summary["status"], "actual_model_runs": summary["actual_model_runs"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
