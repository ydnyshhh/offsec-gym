"""Paired analysis uses shared pretreatment eligibility and seed resampling."""

from __future__ import annotations

import pytest

from offsecgym.research.m652_analysis import (
    ReportingArmRecord,
    ReportingPrefixRecord,
    analyze_paired_reporting,
)


def _row(seed: int, variant: str, *, fresh=(), continuation=(), valid=True):
    digest = f"{seed:064x}"
    return ReportingPrefixRecord(
        seed=seed,
        variant=variant,
        source_trace_sha256=digest,
        source_score_valid=True,
        source_validated_roots=("already",) if variant == "vulnerable" else (),
        complete_proof_roots=("already", "latent-a", "latent-b") if variant == "vulnerable" else (),
        fresh=ReportingArmRecord(
            arm="fresh",
            source_trace_sha256=digest,
            score_valid=valid,
            status="completed" if valid else "provider_failed",
            validated_roots=fresh if valid else (),
            input_tokens=100,
        ),
        continuation=ReportingArmRecord(
            arm="continuation",
            source_trace_sha256=digest,
            score_valid=True,
            status="completed",
            validated_roots=continuation,
            input_tokens=100,
        ),
    )


def test_analysis_counts_discordance_only_on_source_proof_misses() -> None:
    rows = (
        _row(1, "vulnerable", fresh=("latent-a", "already"), continuation=("latent-b",)),
        _row(1, "patched"),
        _row(2, "vulnerable", fresh=("latent-a",), continuation=()),
        _row(2, "patched"),
    )
    result = analyze_paired_reporting(rows, protocol="m652-test")
    assert result["primary"]["eligible_roots"] == 4
    assert result["primary"]["fresh_recovered"] == 2
    assert result["primary"]["continuation_recovered"] == 1
    assert result["primary"]["difference_per_eligible_root"] == 0.25
    assert result["primary"]["fresh_only"] == 2
    assert result["primary"]["continuation_only"] == 1
    assert analyze_paired_reporting(rows, protocol="m652-test") == result


def test_invalid_arm_is_retained_not_scored_as_zero() -> None:
    rows = (_row(1, "vulnerable", valid=False), _row(1, "patched"))
    result = analyze_paired_reporting(rows, protocol="m652-invalid")
    assert len(result["invalid_prefixes"]) == 1
    assert result["primary"]["eligible_roots"] == 0
    assert result["primary"]["difference_per_eligible_root"] is None


def test_mismatched_prefix_or_missing_pair_is_rejected() -> None:
    row = _row(1, "vulnerable")
    with pytest.raises(ValueError, match="both vulnerable and patched"):
        analyze_paired_reporting((row,), protocol="m652-missing")
    with pytest.raises(ValueError, match="source prefix"):
        ReportingPrefixRecord.model_validate(
            {
                **row.model_dump(mode="json"),
                "fresh": {
                    **row.fresh.model_dump(mode="json"),
                    "source_trace_sha256": "f" * 64,
                },
            }
        )
