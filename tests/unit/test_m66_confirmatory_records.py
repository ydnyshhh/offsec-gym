"""Confirmatory records preserve audited failures and missing outcomes."""

from __future__ import annotations

import pytest

from offsecgym.research.m66_confirmatory_records import record_from_audit

CELL = {
    "cell_id": "assigned-a",
    "order": 1,
    "range_family": "saas",
    "seed": 123456,
    "variant": "vulnerable",
    "arm": "witness",
    "arm_order": ["witness", "control"],
}
MANIFEST_SHA = "a" * 64
PROTOCOL = "m66-confirmatory-v1"


def _terminal(*, status="provider_failed", valid=False):
    return {
        "cell_id": CELL["cell_id"],
        "order": CELL["order"],
        "run_id": "run-a",
        "trace_sha256": "b" * 64,
        "status": status,
        "score_valid": valid,
        "evaluation": {"status": status},
        "input_tokens": 100,
        "output_tokens": 10,
        "gateway_actions": 2,
    }


def _stage(terminal, *, complete=True):
    return {
        "protocol": "m66-confirmatory-v1",
        "manifest_sha256": MANIFEST_SHA,
        "cell_id": CELL["cell_id"],
        "trace_sha256": terminal["trace_sha256"],
        "stages": {
            "run_id": terminal["run_id"],
            "range_family": CELL["range_family"],
            "seed": CELL["seed"],
            "variant": CELL["variant"],
            "arm": CELL["arm"],
            "arm_order": CELL["arm_order"],
            "status": terminal["status"],
            "score_valid": terminal["score_valid"],
            "score_replay": terminal["evaluation"],
            "input_tokens": terminal["input_tokens"],
            "output_tokens": terminal["output_tokens"],
            "gateway_actions": terminal["gateway_actions"],
            "patched_submitted_findings": 0,
            "patched_rejected_findings": 0,
            "patched_inconclusive_findings": 0,
            "roots": [{"root": "MEMBER-REFUND", "applicable": True, "complete_witness": complete}],
        },
    }


def test_provider_failed_preterminal_witness_remains_observed() -> None:
    terminal = _terminal()
    row = record_from_audit(
        CELL,
        terminal=terminal,
        stage_output=_stage(terminal),
        manifest_sha256=MANIFEST_SHA,
        protocol=PROTOCOL,
    )
    assert row.status == "provider_failed"
    assert not row.score_valid
    assert row.trace_auditable
    assert row.complete_witness_roots == ("MEMBER-REFUND",)


def test_missing_stage_is_missing_outcome_and_unstarted_is_retained() -> None:
    row = record_from_audit(
        CELL,
        terminal=_terminal(),
        stage_output=None,
        manifest_sha256=MANIFEST_SHA,
        protocol=PROTOCOL,
    )
    assert not row.trace_auditable
    assert row.complete_witness_roots == ()
    unstarted = record_from_audit(
        CELL,
        terminal=None,
        stage_output=None,
        manifest_sha256=MANIFEST_SHA,
        protocol=PROTOCOL,
    )
    assert unstarted.status == "unstarted"
    assert not unstarted.trace_auditable


def test_asymmetric_or_misbound_record_is_rejected() -> None:
    terminal = _terminal()
    stage = _stage(terminal)
    stage["trace_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="frozen cell"):
        record_from_audit(
            CELL,
            terminal=terminal,
            stage_output=stage,
            manifest_sha256=MANIFEST_SHA,
            protocol=PROTOCOL,
        )
    with pytest.raises(ValueError, match="score-valid"):
        record_from_audit(
            CELL,
            terminal=_terminal(valid=True),
            stage_output=None,
            manifest_sha256=MANIFEST_SHA,
            protocol=PROTOCOL,
        )


def test_patched_findings_count_all_submissions() -> None:
    cell = {**CELL, "variant": "patched"}
    terminal = _terminal(status="completed", valid=True)
    stage = _stage(terminal, complete=False)
    stage["stages"]["variant"] = "patched"
    stage["stages"].update(
        patched_submitted_findings=3,
        patched_rejected_findings=1,
        patched_inconclusive_findings=2,
    )
    row = record_from_audit(
        cell,
        terminal=terminal,
        stage_output=stage,
        manifest_sha256=MANIFEST_SHA,
        protocol=PROTOCOL,
    )
    assert row.assigned_roots == ()
    assert row.patched_submitted_findings == 3
    assert row.patched_rejected_findings == 1
    assert row.patched_inconclusive_findings == 2


def test_v2_record_rejects_a_v1_stage_artifact() -> None:
    terminal = _terminal()
    stage = _stage(terminal)
    with pytest.raises(ValueError, match="frozen cell"):
        record_from_audit(
            CELL,
            terminal=terminal,
            stage_output=stage,
            manifest_sha256=MANIFEST_SHA,
            protocol="m66-confirmatory-v2",
        )
    stage["protocol"] = "m66-confirmatory-v2"
    row = record_from_audit(
        CELL,
        terminal=terminal,
        stage_output=stage,
        manifest_sha256=MANIFEST_SHA,
        protocol="m66-confirmatory-v2",
    )
    assert row.complete_witness_roots == ("MEMBER-REFUND",)
