"""The witness policy contrast uses assigned opportunities, not post-treatment successes."""

from __future__ import annotations

import pytest

from offsecgym.research.m66_analysis import WitnessPolicyRecord, analyze_witness_policy


def _row(seed, variant, arm, *, complete=(), successful=(), valid=True):
    return WitnessPolicyRecord(
        seed=seed,
        variant=variant,
        arm=arm,
        score_valid=valid,
        status="completed" if valid else "provider_failed",
        assigned_state_change_roots=("refund",) if variant == "vulnerable" else (),
        successful_transition_roots=successful if valid else (),
        complete_witness_roots=complete if valid else (),
        validated_roots=complete if valid else (),
        http_actions=5,
    )


def test_witness_itt_and_conditional_diagnostic_are_distinct() -> None:
    rows = (
        _row(1, "vulnerable", "control", successful=("refund",)),
        _row(1, "vulnerable", "ledger", successful=("refund",), complete=("refund",)),
        _row(1, "patched", "control"),
        _row(1, "patched", "ledger"),
    )
    result = analyze_witness_policy(rows, protocol="m66-test")
    assert result["primary"]["difference_per_assigned_root"] == 1.0
    assert result["diagnostic_conditional_transition_to_witness"] == {
        "control": 0.0,
        "ledger": 1.0,
    }


def test_invalid_pair_is_visible_and_not_assigned_zero_witnesses() -> None:
    rows = (
        _row(1, "vulnerable", "control", valid=False),
        _row(1, "vulnerable", "ledger"),
        _row(1, "patched", "control"),
        _row(1, "patched", "ledger"),
    )
    result = analyze_witness_policy(rows, protocol="m66-invalid")
    assert len(result["invalid_pairs"]) == 1
    assert result["primary"]["assigned_opportunities"] == 0
    assert result["primary"]["difference_per_assigned_root"] is None


def test_witness_stage_cannot_exceed_successful_transition() -> None:
    with pytest.raises(ValueError, match="exceed assigned"):
        _row(1, "vulnerable", "ledger", complete=("refund",))
