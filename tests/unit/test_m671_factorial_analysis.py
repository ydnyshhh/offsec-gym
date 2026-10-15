"""Seed-block contrasts and assigned missingness, without inferential claims."""

from __future__ import annotations

from pathlib import Path

import pytest

from offsecgym.research.m671_factorial_analysis import analyze_factorial
from offsecgym.research.m671_protocol import assignment_rows, historical_exclusions, select_seeds

ROOT = Path(__file__).parents[2]


def rows() -> list[dict[str, object]]:
    planned = assignment_rows(select_seeds(historical_exclusions(ROOT)[0]))
    return [
        {
            **cell,
            "started": True,
            "trace_auditable": True,
            "score_valid": True,
            "resource_auditable": True,
            "terminal_status": "completed",
            "decisive_root_action_attempted": bool(cell["A"]),
            "decisive_root_action_completed": bool(cell["A"]),
            "target_transition": bool(cell["A"]),
            "root_recorded_finding_after_transition": bool(cell["A"]),
            "model_calls": 2,
            "input_tokens": 10,
            "output_tokens": 5,
            "estimated_cost_usd": "0.001",
            "exploration_actions": 1,
        }
        for cell in planned
    ]


def test_paired_seed_contrasts_and_no_root_pooling() -> None:
    result = analyze_factorial(rows(), bootstrap_replicates=100)
    assert result["no_primary_cross_root_pooling"] is True
    assert set(result["groups"]) == {"B2-REVOKED-ROLE", "B3-CANCELLED-JOB"}
    b2 = result["groups"]["B2-REVOKED-ROLE"]["vulnerable"]
    effects = b2["seed_block_contrasts"]["target_transition"]
    assert effects["complete_seed_blocks"] == 8
    assert effects["contrasts"] == {
        "A_when_B0": 1,
        "A_when_B1": 1,
        "B_when_A0": 0,
        "B_when_A1": 0,
        "A_main": 1,
        "B_main": 0,
        "interaction": 0,
    }
    assert (
        effects
        == analyze_factorial(rows(), bootstrap_replicates=100)["groups"]["B2-REVOKED-ROLE"][
            "vulnerable"
        ]["seed_block_contrasts"]["target_transition"]
    )
    assert b2["arms"]["A0B0"]["endpoints"]["target_transition"]["all_assigned_rate"] == 0
    assert b2["arms"]["A1B0"]["endpoints"]["target_transition"]["all_assigned_rate"] == 1
    assert b2["arms"]["A0B0"]["resources"]["estimated_cost_usd"] == {
        "known": 8,
        "sum": "0.008",
    }


def test_provider_failure_retains_stage_and_resource_unknown_is_not_zero() -> None:
    sample = rows()
    provider = next(row for row in sample if row["root_id"] == "B3-CANCELLED-JOB")
    provider.update(
        terminal_status="provider_failed",
        score_valid=False,
        resource_auditable=False,
        input_tokens=None,
        output_tokens=None,
        estimated_cost_usd=None,
    )
    result = analyze_factorial(sample, bootstrap_replicates=20)
    group = result["groups"]["B3-CANCELLED-JOB"][provider["variant"]]["arms"][provider["arm"]]
    assert group["terminal_statuses"]["provider_failed"] == 1
    assert group["endpoints"]["target_transition"]["known"] == 8
    assert group["resources"]["input_tokens"]["known"] == 7


def test_assigned_missingness_bounds_and_seed_pair_exclusion() -> None:
    sample = rows()
    missing = next(
        row
        for row in sample
        if row["root_id"] == "B2-REVOKED-ROLE" and row["variant"] == "vulnerable"
    )
    missing.update(
        started=False,
        trace_auditable=False,
        score_valid=False,
        resource_auditable=False,
        terminal_status="unstarted",
        target_transition=None,
        input_tokens=None,
        output_tokens=None,
        estimated_cost_usd=None,
    )
    group = analyze_factorial(sample, bootstrap_replicates=20)["groups"]["B2-REVOKED-ROLE"][
        "vulnerable"
    ]
    endpoint = group["arms"][missing["arm"]]["endpoints"]["target_transition"]
    assert endpoint["known"] == 7
    assert endpoint["all_assigned_rate"] is None
    assert endpoint["missingness_bounds"][1] - endpoint["missingness_bounds"][0] == 1 / 8
    assert group["seed_block_contrasts"]["target_transition"]["complete_seed_blocks"] == 7


def test_requires_exact_assignment_frame() -> None:
    with pytest.raises(ValueError, match="128"):
        analyze_factorial(rows()[:-1], bootstrap_replicates=1)
