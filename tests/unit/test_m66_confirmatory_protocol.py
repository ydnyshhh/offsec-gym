"""The confirmatory plan selects fresh seeds without fixture inspection."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

from offsecgym.research.m66_confirmatory_protocol import (
    ENDPOINT_URL,
    PROTOCOL,
    historical_exclusions,
    plan_confirmatory,
    select_seeds,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

from m66_confirmatory_freeze import live_endpoint_receipt  # noqa: E402


def _receipt():
    return {
        "checked_at": "2026-10-07T00:00:00Z",
        "source": ENDPOINT_URL,
        "response_sha256": "0" * 64,
        "model_id": "moonshotai/kimi-k3",
        "endpoint": "Moonshot AI | moonshotai/kimi-k3-20260715",
        "upstream_provider": "Moonshot AI",
        "revision": "moonshotai/kimi-k3-20260715",
        "status": 0,
        "input_usd_per_million_tokens": 3.0,
        "output_usd_per_million_tokens": 15.0,
    }


def test_frozen_prior_snapshot_includes_both_excluded_v2_seeds() -> None:
    excluded = historical_exclusions(ROOT)
    assert len(excluded) == 162
    assert {712868, 551180} <= excluded
    seeds = select_seeds(excluded)
    chosen = [seed for family in seeds for seed in seeds[family]]
    assert len(chosen) == len(set(chosen)) == 120
    assert not set(chosen) & excluded


def test_plan_has_280_balanced_no_replacement_cells() -> None:
    plan = plan_confirmatory(ROOT, protocol_commit="a" * 40, endpoint_receipt=_receipt())
    assert plan["protocol"] == PROTOCOL
    assert plan["planned_trajectories"] == len(plan["cells"]) == 280
    assert plan["maximum_configured_estimated_cost_usd"] == 504.0
    assert plan["cumulative_estimated_cost_stop_usd"] == 504.0
    assert plan["paid_model_calls_authorized"] is False
    assert plan["no_retry_or_replacement"] is True
    assert plan["seed_selection"]["fixture_inspection_before_selection"] is False
    assert len({cell["cell_id"] for cell in plan["cells"]}) == 280
    assert [cell["order"] for cell in plan["cells"]] == list(range(1, 281))
    assert Counter((cell["range_family"], cell["variant"]) for cell in plan["cells"]) == {
        (family, variant): count
        for family in ("saas", "enterprise_change_control_v1")
        for variant, count in (("vulnerable", 120), ("patched", 20))
    }
    for first, second in zip(plan["cells"][::2], plan["cells"][1::2], strict=True):
        assert (first["range_family"], first["seed"], first["variant"]) == (
            second["range_family"],
            second["seed"],
            second["variant"],
        )
        assert [first["arm"], second["arm"]] == first["arm_order"] == second["arm_order"]


def test_public_endpoint_preflight_requires_exact_revision_and_price(monkeypatch) -> None:
    import json

    class Response:
        def __init__(self, raw: bytes):
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _size):
            return self.raw

    selected = {
        "name": "Moonshot AI | moonshotai/kimi-k3-20260715",
        "provider_name": "Moonshot AI",
        "model_id": "moonshotai/kimi-k3",
        "status": 0,
        "pricing": {"prompt": "0.000003", "completion": "0.000015"},
        "supported_parameters": ["tools", "tool_choice", "reasoning_effort"],
    }

    def serve(endpoint):
        monkeypatch.setattr(
            "m66_confirmatory_freeze.urlopen",
            lambda *_args, **_kwargs: Response(
                json.dumps({"data": {"endpoints": [endpoint]}}).encode()
            ),
        )

    serve(selected)
    receipt = live_endpoint_receipt()
    assert receipt["revision"] == "moonshotai/kimi-k3-20260715"
    assert receipt["input_usd_per_million_tokens"] == 3.0
    serve({**selected, "pricing": {**selected["pricing"], "completion": "0.000016"}})
    with pytest.raises(ValueError, match="drifted"):
        live_endpoint_receipt()
