"""Regenerate public R0 reports without Docker, gateway, or provider calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from offsecgym.research.pilot.postcheck import public_reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/research"))
    args = parser.parse_args()
    reports = public_reports(Path.cwd())
    args.output.mkdir(parents=True, exist_ok=True)
    for name, report in reports.items():
        (args.output / name).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"report_count": len(reports), "provider_calls": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
