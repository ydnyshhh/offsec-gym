"""Independently check the sanitized T1.2 static report and preserved A1 baseline."""

from __future__ import annotations

import json
from pathlib import Path

from offsecgym.benchmark.t12_static_postcheck import verify

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "docs/benchmark/t12-static-prequalification.json"
A1_BASELINE = ROOT / "docs/benchmark/t11f-a1-admission.json"
POSTCHECK = ROOT / "docs/benchmark/t12-static-postcheck.json"


def main() -> None:
    result = verify(PUBLIC, checkout=ROOT, a1_baseline=A1_BASELINE)
    POSTCHECK.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "checks": len(result["checks"])}, sort_keys=True))
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
