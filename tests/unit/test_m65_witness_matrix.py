"""Held-out selection is deterministic and refuses pre-freeze builds."""

import json
from pathlib import Path

import pytest

from offsecgym.research.m65_witness_matrix import (
    maximum_cell_token_cost,
    plan_witness_matrix,
)
from offsecgym.research.m651_seed_selection import select_seeds
from offsecgym.schemas.specs import Budget, ReporterBudget


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


def test_output_cap_bounds_amended_cost_without_weakening_token_cap() -> None:
    probe = Budget(max_total_tokens=120_000, max_model_calls=20, max_output_tokens_per_call=8192)
    reporter = ReporterBudget(
        max_total_tokens=80_000,
        max_model_calls=4,
        max_output_tokens_per_call=4096,
        max_retrieval_calls=24,
        max_finding_submissions=12,
    )
    ceiling = maximum_cell_token_cost(
        probe, reporter, {"input_usd_per_million": 3.0, "output_usd_per_million": 15.0}
    )
    assert ceiling == pytest.approx(2.236608)
    assert ceiling * 20 < 45
    assert ceiling + 1.304514 < 4.5
