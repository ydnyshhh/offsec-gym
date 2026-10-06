"""Confirmatory ITT retains score-invalid but auditable evidence."""

from __future__ import annotations

import pytest

from offsecgym.research.m66_confirmatory_analysis import (
    ROOTS,
    ConfirmatoryRecord,
    analyze_confirmatory,
)


def _row(
    family,
    seed,
    variant,
    arm,
    *,
    complete=(),
    status="completed",
    auditable=True,
    valid=True,
    submitted=0,
):
    return ConfirmatoryRecord(
        range_family=family,
        seed=seed,
        variant=variant,
        arm=arm,
        status=status,
        trace_auditable=auditable,
        score_valid=valid,
        assigned_roots=ROOTS[family] if variant == "vulnerable" else (),
        complete_witness_roots=complete,
        patched_submitted_findings=submitted,
    )


def _blocks(*, control_a=None, witness_a=None):
    a, b = "saas", "enterprise_change_control_v1"
    return (
        control_a or _row(a, 7, "vulnerable", "control"),
        witness_a or _row(a, 7, "vulnerable", "witness", complete=("MEMBER-REFUND",)),
        _row(b, 7, "vulnerable", "control"),
        _row(b, 7, "vulnerable", "witness", complete=("B1-SOD",)),
        _row(a, 7, "patched", "control", submitted=1),
        _row(a, 7, "patched", "witness"),
    )


def _analyze(rows, *, assigned_rows=None):
    expected = assigned_rows if assigned_rows is not None else rows
    return analyze_confirmatory(
        rows,
        protocol="test",
        assigned_keys=frozenset((r.range_family, r.seed, r.variant, r.arm) for r in expected),
    )


def test_provider_failed_auditable_witness_remains_in_itt() -> None:
    rows = _blocks(
        witness_a=_row(
            "saas",
            7,
            "vulnerable",
            "witness",
            complete=("MEMBER-REFUND",),
            status="provider_failed",
            valid=False,
        )
    )
    result = _analyze(rows)
    assert result["primary_by_family"]["saas"]["difference_per_assigned_root"] == 1.0
    assert result["secondary_score_valid_matched"]["saas"]["matched_score_valid_pairs"] == 0
    assert result["primary_by_root"]["B1-SOD"]["difference_per_assigned_root"] == 1.0
    assert result["patched_findings"]["saas"]["control"]["submitted_findings_observed"] == 1


def test_unauditable_arm_is_missing_with_all_assigned_denominator_and_bounds() -> None:
    rows = _blocks(
        control_a=_row(
            "saas",
            7,
            "vulnerable",
            "control",
            status="provider_failed",
            valid=False,
            auditable=False,
        )
    )
    result = _analyze(rows)
    primary = result["primary_by_family"]["saas"]
    assert primary["assigned_root_opportunities"] == 1
    assert primary["difference_per_assigned_root"] is None
    assert primary["worst_best_bounds"] == [0.0, 1.0]
    assert primary["seed_bootstrap_95pct"] is None
    assert result["secondary_pooled"]["assigned_root_opportunities"] == 4


def test_unstarted_arm_is_retained_as_missing() -> None:
    rows = _blocks(
        witness_a=_row(
            "saas",
            7,
            "vulnerable",
            "witness",
            status="unstarted",
            valid=False,
            auditable=False,
        )
    )
    primary = _analyze(rows)["primary_by_family"]["saas"]
    assert primary["assigned_root_opportunities"] == 1
    assert primary["witness_missing_root_outcomes"] == 1
    assert primary["worst_best_bounds"] == [0.0, 1.0]


def test_family_key_allows_same_seed_and_patched_subset() -> None:
    result = _analyze(_blocks())
    assert result["vulnerable_assigned_pairs"] == 2
    assert (
        result["patched_findings"]["enterprise_change_control_v1"]["control"]["assigned_cells"] == 0
    )


def test_missing_arm_and_invalid_ontology_are_rejected() -> None:
    with pytest.raises(ValueError, match="missing an arm"):
        _analyze(_blocks()[:-1])
    with pytest.raises(ValueError, match="differ from frozen assigned"):
        _analyze(_blocks()[:-1], assigned_rows=_blocks())
    with pytest.raises(ValueError, match="ontology"):
        ConfirmatoryRecord(
            range_family="saas",
            seed=1,
            variant="vulnerable",
            arm="control",
            status="completed",
            score_valid=True,
            trace_auditable=True,
            assigned_roots=("wrong",),
        )
