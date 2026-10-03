"""The reporter pilot is outside the sample and rejects over-cost launches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from offsecgym.research.m65_witness_pilot import (
    PILOT_MAX_ESTIMATED_USD,
    PILOT_SEED,
    plan_pilot,
    run_pilot,
    verify_pilot,
)


def test_pilot_pair_is_separate_and_pinned(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    planned = plan_pilot(root)
    assert PILOT_SEED == 2101
    assert [item["variant"] for item in planned["cells"]] == ["vulnerable", "patched"]
    assert len({item["pair_id"] for item in planned["cells"]}) == 1
    assert planned["maximum_estimated_token_cost_usd"] == PILOT_MAX_ESTIMATED_USD == 6.0
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(planned))
    assert verify_pilot(root, path) == planned
    edited = {**planned, "seed": 2001}
    path.write_text(json.dumps(edited))
    with pytest.raises(ValueError, match="differs"):
        verify_pilot(root, path)


@pytest.mark.asyncio
async def test_pilot_rejects_unapproved_cost_and_missing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[2]
    manifest = tmp_path / "pilot.json"
    manifest.write_text(json.dumps(plan_pilot(root)))
    journal = tmp_path / "journal.jsonl"
    args = (root, manifest, journal, tmp_path / "state")
    with pytest.raises(ValueError, match="reviewed ceiling"):
        await run_pilot(*args, max_estimated_usd=6.01)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OFFSECGYM_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="required"):
        await run_pilot(*args, max_estimated_usd=6.0)
    assert not journal.exists()
