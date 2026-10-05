"""M6.5.2 predeclares one model, one budget, fresh paired seeds, and cost stops."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from offsecgym.research import m652_execute, m652_matrix
from offsecgym.research.m652_matrix import plan_m652

ROOT = Path(__file__).parents[2]
COMMIT = "a" * 40


def _selection(root: Path) -> dict[str, object]:
    return {
        "selection_source_commit": COMMIT,
        "sample_seeds": list(range(5001, 5025)),
        "pilot_seed": 5999,
        "excluded_seeds": [],
    }


def _pairs(root: Path, base, seeds) -> dict[str, object]:
    return {
        str(seed): {
            "pair_id": str(uuid4()),
            "vulnerable_build_id": str(uuid4()),
            "patched_build_id": str(uuid4()),
            "fixture_sha256": "a" * 64,
        }
        for seed in seeds
    }


def test_single_budget_matrix_has_24_fresh_pairs_and_one_pilot(monkeypatch) -> None:
    monkeypatch.setattr(m652_matrix, "_selection", _selection)
    monkeypatch.setattr(m652_matrix, "_pair_records", _pairs)
    monkeypatch.setattr(
        m652_matrix,
        "SEED_MANIFEST",
        "experiments/manifests/m651-witness-seed-selection-v1.json",
    )
    sample = plan_m652(ROOT, source_commit=COMMIT)
    pilot = plan_m652(ROOT, source_commit=COMMIT, pilot=True)
    assert len(sample["seed_set"]) == 24
    assert sample["planned_live_cells"] == 48
    assert len(pilot["cells"]) == 1
    assert pilot["seed_set"] == (5999,)
    assert set(sample["seed_set"]).isdisjoint(pilot["seed_set"])
    assert all(
        {cell["variant"] for cell in sample["cells"] if cell["range_seed"] == seed}
        == {"vulnerable", "patched"}
        for seed in sample["seed_set"]
    )
    assert sample["probe_budget"]["max_model_calls"] == 10
    assert sample["reporter_budget_per_arm"]["max_model_calls"] == 4
    assert sample["probe_budget"]["max_total_tokens"] == 120_000
    assert sample["reporter_budget_per_arm"]["max_total_tokens"] == 80_000
    assert sample["model_request"]["reasoning"] == "high"
    assert sample["expected_selected_endpoint"] == {
        "revision": "moonshotai/kimi-k3-20260715",
        "upstream_provider": "Moonshot AI",
    }
    assert sample["maximum_estimated_token_cost_usd"] == 106.380288
    assert sample["maximum_estimated_token_cost_usd"] < sample["cumulative_estimated_cost_stop_usd"]
    assert pilot["maximum_estimated_token_cost_usd"] == 2.216256


def test_selected_endpoint_price_drift_is_a_prepaid_gate() -> None:
    manifest = {
        "expected_selected_endpoint": {
            "revision": "moonshotai/kimi-k3-20260715",
            "upstream_provider": "Moonshot AI",
        },
        "price_snapshot": {
            "input_usd_per_million": 3.0,
            "output_usd_per_million": 15.0,
        },
    }
    metadata = {
        "data": {
            "endpoints": [
                {
                    "name": "Moonshot AI | moonshotai/kimi-k3-20260715",
                    "provider_name": "Moonshot AI",
                    "pricing": {"prompt": "0.000003", "completion": "0.000015"},
                }
            ]
        }
    }
    m652_execute.verify_live_price(manifest, metadata)
    metadata["data"]["endpoints"][0]["pricing"]["completion"] = "0.000016"
    with pytest.raises(ValueError, match="price differs"):
        m652_execute.verify_live_price(manifest, metadata)


@pytest.mark.asyncio
async def test_cost_ceiling_rejects_before_loading_provider_credentials(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        m652_execute,
        "verify_manifest",
        lambda root, path: {"cumulative_estimated_cost_stop_usd": 108.0},
    )
    with pytest.raises(ValueError, match="cost differs"):
        await m652_execute.execute(
            ROOT,
            tmp_path / "manifest.json",
            tmp_path / "journal.jsonl",
            tmp_path / "state",
            max_estimated_usd=108.01,
        )
