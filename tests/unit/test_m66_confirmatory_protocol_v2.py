"""The replacement assignment excludes every seed already assigned in v1."""

from __future__ import annotations

import json
from pathlib import Path

from offsecgym.research.m66_confirmatory_protocol_v2 import (
    PROTOCOL,
    historical_exclusions,
    plan_confirmatory,
    select_seeds,
)

ROOT = Path(__file__).resolve().parents[2]


def test_v2_seed_registry_excludes_the_entire_stopped_v1_assignment() -> None:
    prior = json.loads((ROOT / "experiments/manifests/m66-confirmatory-v1.json").read_bytes())
    v1_assigned = {cell["seed"] for cell in prior["cells"]}
    excluded = historical_exclusions(ROOT)
    selected = select_seeds(excluded)
    fresh = {seed for family in selected.values() for seed in family}
    assert len(v1_assigned) == 120
    assert len(excluded) == 282
    assert len(fresh) == 120
    assert v1_assigned <= excluded
    assert not fresh & excluded


def test_v2_plan_preserves_policy_and_requires_separate_approval() -> None:
    receipt = {
        "checked_at": "2026-10-07T00:00:00Z",
        "source": "https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints",
        "response_sha256": "0" * 64,
        "model_id": "moonshotai/kimi-k3",
        "endpoint": "Moonshot AI | moonshotai/kimi-k3-20260715",
        "upstream_provider": "Moonshot AI",
        "revision": "moonshotai/kimi-k3-20260715",
        "status": 0,
        "input_usd_per_million_tokens": 3.0,
        "output_usd_per_million_tokens": 15.0,
    }
    plan = plan_confirmatory(ROOT, protocol_commit="a" * 40, endpoint_receipt=receipt)
    assert plan["protocol"] == PROTOCOL
    assert len(plan["cells"]) == 280
    assert len({cell["cell_id"] for cell in plan["cells"]}) == 280
    assert plan["paid_model_calls_authorized"] is False
    assert plan["no_retry_or_replacement"] is True
    assert plan["maximum_configured_estimated_cost_usd"] == 504.0
    assert plan["seed_selection"]["historical_exclusion_count"] == 282
    assert "src/offsecgym/research/m65_witness_packet.py" in plan["source_file_hashes"]
