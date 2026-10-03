"""The live pilot is bounded, separate from sample cells, and fail closed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from offsecgym.research.m65_monolithic_pilot import (
    EXPECTED_BOOTSTRAP_SHA256,
    EXPECTED_PAIR_ID,
    PER_RUN_USD_CAP,
    PILOT_SEED,
    PILOT_TOKENS,
    TOTAL_ESTIMATED_USD_CAP,
    pilot_spec,
    run_pilot,
)


def test_m65_pilot_is_separate_and_has_prices_for_hard_run_budget() -> None:
    root = Path(__file__).parents[2]
    manifest = json.loads(
        (root / "experiments/manifests/m65-common-bootstrap-monolithic-v1.json").read_text()
    )
    assert PILOT_SEED not in manifest["seed_set"]
    assert EXPECTED_PAIR_ID not in {pair["pair_id"] for pair in manifest["range_pairs"].values()}
    assert len(EXPECTED_BOOTSTRAP_SHA256) == 64
    assert TOTAL_ESTIMATED_USD_CAP == 2 * PER_RUN_USD_CAP
    for patched in (False, True):
        spec = pilot_spec(root, manifest, patched=patched)
        assert spec.range.seed == PILOT_SEED
        assert spec.range.patched is patched
        assert spec.budget.max_total_tokens == PILOT_TOKENS
        assert spec.budget.max_cost_usd == PER_RUN_USD_CAP
        assert spec.model.input_usd_per_million_tokens == 3.0
        assert spec.model.output_usd_per_million_tokens == 15.0
        assert spec.bootstrap_budget.max_total_tokens == 0


@pytest.mark.asyncio
async def test_m65_pilot_requires_key_and_database_before_any_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OFFSECGYM_DATABASE_URL", raising=False)
    root = Path(__file__).parents[2]
    with pytest.raises(ValueError, match="required"):
        await run_pilot(
            root,
            root / "experiments/manifests/m65-common-bootstrap-monolithic-v1.json",
            tmp_path / "journal.jsonl",
            tmp_path / "state",
        )
    assert not (tmp_path / "journal.jsonl").exists()
