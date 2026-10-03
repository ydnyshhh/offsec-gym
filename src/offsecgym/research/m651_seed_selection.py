"""Select future paired assay seeds without building or inspecting any range."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

PROTOCOL = "m651-witness-recovery-v1"
SEED_MIN = 3000
SEED_MAX = 999_999
SAMPLE_COUNT = 10
EXPLICIT_EXCLUSIONS = {
    *range(1001, 1011),  # completed M6.4/M6.5 matrices
    1101,  # completed M6.5 monolithic pilot
    *range(2001, 2011),  # prior witness-contract branch manifest/builds
    2101,  # prior witness-contract branch pilot manifest
}


def _seed_from_digest(source_commit: str, label: str, counter: int) -> int:
    raw = f"{PROTOCOL}|{label}|{source_commit}|{counter}".encode()
    return SEED_MIN + int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % (
        SEED_MAX - SEED_MIN + 1
    )


def _walk_seeds(value: object) -> set[int]:
    found: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key in {"seed", "range_seed"}
                and isinstance(item, int)
                and not isinstance(item, bool)
            ):
                found.add(item)
            elif key in {"seed_set", "seeds"} and isinstance(item, list):
                found.update(x for x in item if isinstance(x, int) and not isinstance(x, bool))
            found.update(_walk_seeds(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_walk_seeds(item))
    return found


def collect_exclusions(root: Path) -> set[int]:
    excluded = set(EXPLICIT_EXCLUSIONS)
    for folder in (
        root / "experiments" / "manifests",
        root / "experiments" / "configs",
        root / "docs" / "diagnostics",
        root / ".offsecgym" / "diagnostics",
    ):
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if (
                not path.is_file()
                or path.suffix not in {".json", ".jsonl", ".yaml", ".yml"}
                or path.stat().st_size > 2_000_000
            ):
                continue
            if path.suffix == ".jsonl":
                for line in path.read_text().splitlines():
                    if line.strip():
                        excluded.update(_walk_seeds(json.loads(line)))
                continue
            data = (
                json.loads(path.read_text())
                if path.suffix == ".json"
                else yaml.safe_load(path.read_text())
            )
            excluded.update(_walk_seeds(data))
    return excluded


def select_seeds(root: Path, *, source_commit: str) -> dict[str, object]:
    if len(source_commit) != 40 or any(c not in "0123456789abcdef" for c in source_commit):
        raise ValueError("seed selection requires a full source commit SHA")
    excluded = collect_exclusions(root)
    selected: list[int] = []
    counters: list[int] = []
    counter = 0
    while len(selected) < SAMPLE_COUNT:
        candidate = _seed_from_digest(source_commit, "sample", counter)
        if candidate not in excluded and candidate not in selected:
            selected.append(candidate)
            counters.append(counter)
        counter += 1
    pilot_counter = 0
    while True:
        pilot = _seed_from_digest(source_commit, "pilot", pilot_counter)
        if pilot not in excluded and pilot not in selected:
            break
        pilot_counter += 1
    return {
        "protocol": PROTOCOL,
        "schema_version": "1",
        "selection_source_commit": source_commit,
        "algorithm": (
            "SHA-256(protocol|label|source_commit|counter), "
            "first eight bytes modulo documented range"
        ),
        "seed_range": [SEED_MIN, SEED_MAX],
        "excluded_seeds": sorted(excluded),
        "exclusion_scan": (
            "explicit prior branch plus structured JSON/YAML/JSONL manifests, "
            "configs and diagnostics up to 2 MB"
        ),
        "sample_seeds": selected,
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
    result = select_seeds(args.repository_root, source_commit=args.source_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"sample_seeds": result["sample_seeds"], "pilot_seed": result["pilot_seed"]}))


if __name__ == "__main__":
    main()
