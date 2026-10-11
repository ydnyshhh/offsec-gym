"""Immutable historical-seed registry gate for a future M6.6.1 manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REGISTRY_PATH = Path("experiments/manifests/m661-prior-seed-exclusions-v1.json")


def load_exclusion_registry(repository_root: Path) -> tuple[frozenset[int], str]:
    """Verify pinned predecessor bytes; never scan mutable diagnostics state."""
    path = repository_root / REGISTRY_PATH
    raw = path.read_bytes()
    registry = json.loads(raw)
    if registry.get("schema_version") != "1" or registry.get("registry") != (
        "m661-prior-seed-exclusions-v1"
    ):
        raise ValueError("M6.6.1 exclusion registry version differs")
    sources = registry.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        raise ValueError("M6.6.1 exclusion sources differ")
    for source in sources:
        source_path = repository_root / source["path"]
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("M6.6.1 historical source is missing or unsafe")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("M6.6.1 historical source hash differs")
    prior = json.loads((repository_root / sources[0]["path"]).read_bytes())
    confirmatory = json.loads((repository_root / sources[1]["path"]).read_bytes())
    qualified = registry.get("qualification_seeds")
    if not isinstance(qualified, list) or len(qualified) != len(set(qualified)):
        raise ValueError("M6.6.1 qualification seed list is invalid")
    expected = (
        set(prior["excluded_seeds"])
        | {cell["seed"] for cell in confirmatory["cells"]}
        | set(qualified)
    )
    excluded = registry.get("excluded_seeds")
    if excluded != sorted(expected) or any(type(seed) is not int or seed < 0 for seed in excluded):
        raise ValueError("M6.6.1 excluded seeds differ from pinned source union")
    return frozenset(expected), hashlib.sha256(raw).hexdigest()


def require_fresh_seed(repository_root: Path, seed: int) -> str:
    excluded, digest = load_exclusion_registry(repository_root)
    if seed in excluded:
        raise ValueError("M6.6.1 seed was used in prior or qualification work")
    return digest
