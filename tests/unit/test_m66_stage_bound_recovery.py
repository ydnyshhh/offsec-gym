"""The cell-162 offline audit exception stays exact and paid work stays closed."""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m66_resume_after_stage_bound as recovery  # noqa: E402
import m66_stage_bound_offline as offline  # noqa: E402


def _manifest() -> dict:
    return json.loads((ROOT / "experiments/manifests/m66-confirmatory-v2.json").read_bytes())


def test_paid_stage_continuation_has_no_implicit_approval(tmp_path) -> None:
    path = tmp_path / "approval.json"
    with pytest.raises(ValueError, match="lacks separate paid continuation approval"):
        recovery._require_stage_approval(path)
    path.write_text("{}")
    with pytest.raises(ValueError, match="artifact hash differs"):
        recovery._require_stage_approval(path)


def test_stage_approval_scope_is_exact(tmp_path, monkeypatch) -> None:
    path = tmp_path / "approval.json"
    receipt = {
        "protocol": "m66-confirmatory-v2-stage-bound-reconciliation-v1",
        "approved_for_paid_calls": True,
        "approval_scope": "assigned_cells_163_through_280_only_no_retry_or_replacement",
        "original_manifest_sha256": recovery.EXPECTED_MANIFEST_SHA256,
        "original_approval_sha256": recovery.EXPECTED_APPROVAL_SHA256,
        "stop_journal_sha256": recovery.STOP_JOURNAL_SHA256,
        "source_run_status": "agent_failed",
        "stage_reconstruction_packet_chars": offline.EXACT_PACKET_CHARS,
        "cost_ceiling_usd": 504.0,
        "retained_invalid_cell_orders": [89, 90],
        "retained_agent_failed_cell_order": 162,
        "reviewed_pr": 24,
        "reviewed_pr_head": recovery.REVIEWED_PR_HEAD,
    }
    path.write_text(json.dumps(receipt))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(recovery, "EXPECTED_STAGE_APPROVAL_SHA256", digest)
    assert recovery._require_stage_approval(path) == digest
    receipt["retained_agent_failed_cell_order"] = 161
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(
        recovery, "EXPECTED_STAGE_APPROVAL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="does not cover this exact stop"):
        recovery._require_stage_approval(path)


def test_stop_checkpoint_cannot_gain_an_extra_cell(tmp_path, monkeypatch) -> None:
    manifest = _manifest()
    endpoint = manifest["expected_selected_endpoint"]
    rows = [
        {
            "type": "batch_started",
            "manifest_sha256": recovery.EXPECTED_MANIFEST_SHA256,
            "expected_model_revision": endpoint["revision"],
            "expected_upstream_provider": endpoint["upstream_provider"],
            "max_estimated_usd": 504.0,
        }
    ]
    for order, cell in enumerate(manifest["cells"][:162], 1):
        rows.append({"type": "cell_started", "cell_id": cell["cell_id"], "order": order})
        terminal = {
            "type": "cell_completed",
            "cell_id": cell["cell_id"],
            "order": order,
            "run_id": f"run-{order}",
            "estimated_cost_usd": 0.0,
        }
        if order == 161:
            terminal["run_id"] = recovery.CELL_161_RUN_ID
        if order == 162:
            terminal.update(
                {
                    "run_id": recovery.CELL_162_RUN_ID,
                    "trace_sha256": recovery.CELL_162_TRACE_SHA256,
                    "status": "agent_failed",
                    "failure_reason": "ValidationError",
                    "score_valid": True,
                }
            )
        rows.append(terminal)
    journal = tmp_path / "journal.jsonl"
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(
        recovery, "STOP_JOURNAL_SHA256", hashlib.sha256(journal.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(recovery, "STOP_COST", Decimal(0))
    assert len(recovery._require_stop(manifest, journal)[0]) == 162
    journal.write_bytes(journal.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="audited stop"):
        recovery._require_stop(manifest, journal)


def test_pair_81_receipt_preserves_retained_run_identities() -> None:
    receipt = recovery._retrospective_pair_receipt(
        audits=[{"cell_id": recovery.CELL_161_ID}, {"cell_id": recovery.CELL_ID}],
        preflight_sha256="a" * 64,
        cost=Decimal("71.985765"),
        amendment_sha256="b" * 64,
    )
    assert receipt["protocol"] == _manifest()["protocol"]
    assert receipt["cell_ids"] == [recovery.CELL_161_ID, recovery.CELL_ID]
    assert receipt["run_ids"] == [recovery.CELL_161_RUN_ID, recovery.CELL_162_RUN_ID]
    assert receipt["retrospective_stage_bound_closure"] is True
    with pytest.raises(ValueError, match="both authoritative audits"):
        recovery._retrospective_pair_receipt(
            audits=[{"cell_id": recovery.CELL_161_ID}],
            preflight_sha256="a" * 64,
            cost=Decimal("71.985765"),
            amendment_sha256="b" * 64,
        )


def test_stage_output_is_create_only(tmp_path) -> None:
    path = tmp_path / "stage.json"
    assert (
        recovery._write_bytes_create_only(path, b"exact bytes\n")
        == hashlib.sha256(b"exact bytes\n").hexdigest()
    )
    with pytest.raises(FileExistsError):
        recovery._write_bytes_create_only(path, b"different bytes\n")
    assert path.read_bytes() == b"exact bytes\n"
