"""The paid continuation requires its exact separate approval receipt."""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m66_resume_after_cell208_redaction as recovery  # noqa: E402


def test_paid_recovery_fails_closed_with_zero_pin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(recovery, "EXPECTED_CELL208_APPROVAL_SHA256", "0" * 64)
    path = tmp_path / "approval.json"
    with pytest.raises(ValueError, match="lacks separate exact paid-call approval"):
        recovery._require_approval(path)
    path.write_text("{}")
    with pytest.raises(ValueError, match="lacks separate exact paid-call approval"):
        recovery._require_approval(path)


def test_approval_covers_only_exact_stop_and_health_epoch(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "approval.json"
    value = {
        "protocol": recovery.PROTOCOL,
        "approved_for_paid_calls": True,
        "approval_scope": "assigned_cells_209_through_280_only_no_retry_or_replacement",
        "original_manifest_sha256": recovery.EXPECTED_MANIFEST_SHA256,
        "original_approval_sha256": recovery.EXPECTED_APPROVAL_SHA256,
        "prior_metadata_approval_sha256": recovery.EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256,
        "stop_journal_sha256": recovery.STOP_JOURNAL_SHA256,
        "stage_sha256": recovery.EXPECTED_STAGE_SHA256,
        "provider_health_epoch_start_order": 189,
        "new_provider_health_epoch": False,
        "retained_invalid_cell_orders": recovery.RETAINED_INVALID_ORDERS,
        "retained_agent_failed_cell_order": 162,
        "cost_ceiling_usd": 504,
    }
    path.write_text(json.dumps(value))
    monkeypatch.setattr(
        recovery, "EXPECTED_CELL208_APPROVAL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert recovery._require_approval(path) == recovery.EXPECTED_CELL208_APPROVAL_SHA256
    value["new_provider_health_epoch"] = True
    path.write_text(json.dumps(value))
    monkeypatch.setattr(
        recovery, "EXPECTED_CELL208_APPROVAL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="does not cover this exact stop"):
        recovery._require_approval(path)


def test_pair_receipt_retains_original_runs() -> None:
    receipt = recovery._pair_receipt(
        [{"cell_id": recovery.CELL_207_ID}, {"cell_id": recovery.CELL_ID}],
        Decimal("91.340037"),
        "a" * 64,
    )
    assert receipt["run_ids"] == [recovery.CELL_207_RUN_ID, recovery.RUN_ID]
    assert receipt["cumulative_estimated_cost_usd"] == "91.340037"
    with pytest.raises(ValueError, match="both authoritative audits"):
        recovery._pair_receipt([{"cell_id": recovery.CELL_ID}], Decimal(0), "a" * 64)
