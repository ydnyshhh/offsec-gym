"""Held-out selection is deterministic and refuses pre-freeze builds."""

import json
from pathlib import Path

import pytest

from offsecgym.research.m65_witness_matrix import plan_witness_matrix
from offsecgym.research.m651_seed_selection import select_seeds


def test_seed_selection_excludes_prior_and_local_configs_without_building(tmp_path: Path) -> None:
    manifests = tmp_path / "experiments" / "manifests"
    configs = tmp_path / "experiments" / "configs"
    manifests.mkdir(parents=True)
    configs.mkdir(parents=True)
    (manifests / "old.json").write_text(json.dumps({"cells": [{"range_seed": 5500}]}))
    (configs / "old.yaml").write_text("range:\n  seed: 4500\n")
    first = select_seeds(tmp_path, source_commit="f" * 40)
    assert first == select_seeds(tmp_path, source_commit="f" * 40)
    assert len(first["sample_seeds"]) == len(set(first["sample_seeds"])) == 10
    assert set(first["sample_seeds"]).isdisjoint(first["excluded_seeds"])
    assert first["pilot_seed"] not in first["sample_seeds"]
    assert {1001, 1010, 1101, 2001, 2010, 2101, 4500, 5500}.issubset(first["excluded_seeds"])
    assert not (tmp_path / "builds").exists()


def test_matrix_refuses_builds_without_committed_seed_declaration(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        plan_witness_matrix(tmp_path, source_commit="f" * 40)
