"""Commit a fresh 24-pair reporting-context sample before inspecting fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from offsecgym.research.m651_seed_selection import collect_exclusions

PROTOCOL = "m652-reporting-context-seeds-v1"
SAMPLE_COUNT = 24
SEED_MIN = 3000
SEED_MAX = 999_999


def _candidate(source_commit: str, label: str, counter: int) -> int:
    raw = f"{PROTOCOL}|{label}|{source_commit}|{counter}".encode()
    return SEED_MIN + int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % (
        SEED_MAX - SEED_MIN + 1
    )


def select_m652_seeds(root: Path, *, source_commit: str) -> dict[str, object]:
    if len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
        raise ValueError("seed selection requires a full committed source SHA")
    excluded = collect_exclusions(root)
    seeds: list[int] = []
    counters: list[int] = []
    counter = 0
    while len(seeds) < SAMPLE_COUNT:
        seed = _candidate(source_commit, "sample", counter)
        if seed not in excluded and seed not in seeds:
            seeds.append(seed)
            counters.append(counter)
        counter += 1
    pilot_counter = 0
    while True:
        pilot = _candidate(source_commit, "pilot", pilot_counter)
        if pilot not in excluded and pilot not in seeds:
            break
        pilot_counter += 1
    return {
        "protocol": PROTOCOL,
        "schema_version": "1",
        "selection_source_commit": source_commit,
        "algorithm": "SHA-256(protocol|label|commit|counter), first eight bytes modulo range",
        "seed_range": [SEED_MIN, SEED_MAX],
        "excluded_seeds": sorted(excluded),
        "sample_seeds": seeds,
        "sample_counters": counters,
        "pilot_seed": pilot,
        "pilot_counter": pilot_counter,
        "range_inspection_before_freeze": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = select_m652_seeds(args.repository_root, source_commit=args.source_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"sample_seeds": result["sample_seeds"], "pilot_seed": result["pilot_seed"]}))


if __name__ == "__main__":
    main()
