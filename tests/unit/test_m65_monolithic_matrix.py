"""The M6.5 control matrix is paired, deterministic, and separately pinned."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from offsecgym.experiment.scripted import experiment_hash
from offsecgym.research.m65_monolithic_matrix import (
    CONFIG,
    HISTORICAL_MANIFEST,
    _cell_spec,
    plan_m65_monolithic_matrix,
)
from offsecgym.schemas.specs import ExperimentSpec


def test_m65_monolithic_matrix_matches_historical_pairs_and_caps() -> None:
    root = Path(__file__).parents[2]
    planned = plan_m65_monolithic_matrix(root, source_commit="9ddac19")
    assert planned == plan_m65_monolithic_matrix(root, source_commit="9ddac19")
    assert planned["protocol"] == "m65-common-bootstrap-monolithic-v1"
    assert planned["historical_comparison"] is True
    assert planned["planned_live_cells"] == 100
    assert planned["maximum_configured_model_tokens"] == 9_200_000
    assert planned["maximum_configured_model_calls"] == 2_000
    assert [cell["order"] for cell in planned["cells"]] == list(range(1, 101))
    assert len({cell["cell_id"] for cell in planned["cells"]}) == 100
    assert Counter(cell["model_token_budget"] for cell in planned["cells"]) == {
        40000: 20,
        60000: 20,
        80000: 20,
        120000: 20,
        160000: 20,
    }
    historical = json.loads((root / HISTORICAL_MANIFEST).read_text())
    base = ExperimentSpec.model_validate(yaml.safe_load((root / CONFIG).read_text()))
    for cell in planned["cells"]:
        pair = historical["range_pairs"][str(cell["range_seed"])]
        assert cell["pair_id"] == pair["pair_id"]
        assert cell["build_id"] == pair[f"{cell['variant']}_build_id"]
        spec = _cell_spec(
            base,
            cell["range_seed"],
            cell["variant"] == "patched",
            cell["model_token_budget"],
        )
        assert cell["experiment_sha256"] == experiment_hash(spec)


def test_m65_monolithic_matrix_rejects_missing_source_commit() -> None:
    with pytest.raises(ValueError, match="source commit"):
        plan_m65_monolithic_matrix(Path(__file__).parents[2], source_commit="")
