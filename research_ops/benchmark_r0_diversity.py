"""Print a source-bound static task-diversity audit without running a range."""

from __future__ import annotations

import argparse
import json

from offsecgym.benchmark.task_diversity_audit import diversity_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    print(json.dumps(diversity_audit(args.seed), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
