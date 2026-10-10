"""The cell-192 metadata stop can resume only from exact frozen evidence."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m66_confirmatory_collect as collector  # noqa: E402
import m66_resume_after_metadata_failure as recovery  # noqa: E402


def _manifest() -> dict:
    return json.loads((ROOT / "experiments/manifests/m66-confirmatory-v2.json").read_bytes())


def _approval() -> dict:
    return {
        "protocol": recovery.PROTOCOL,
        "approved_for_paid_calls": True,
        "approval_scope": "assigned_cells_193_through_280_only_no_retry_or_replacement",
        "original_manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "original_approval_sha256": collector.EXPECTED_APPROVAL_SHA256,
        "prior_pause_approval_sha256": collector.EXPECTED_PAUSE_APPROVAL_SHA256,
        "prior_stage_approval_sha256": recovery.EXPECTED_STAGE_APPROVAL_SHA256,
        "prior_rate_approval_sha256": collector.EXPECTED_RATE_PAUSE_APPROVAL_SHA256,
        "stop_journal_sha256": collector.METADATA_STOP_JOURNAL_SHA256,
        "cost_ceiling_usd": 504.0,
        "provider_health_epoch_start_order": 189,
        "new_provider_health_epoch": False,
        "retained_invalid_cell_orders": recovery.RETAINED_INVALID_ORDERS,
        "retained_agent_failed_cell_order": 162,
    }


def test_paid_continuation_requires_new_exact_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(recovery, "EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256", "0" * 64)
    path = tmp_path / "metadata-approval.json"
    with pytest.raises(ValueError, match="lacks separate exact paid-call approval"):
        recovery._require_metadata_approval(path)
    path.write_text(json.dumps(_approval()))
    with pytest.raises(ValueError, match="lacks separate exact paid-call approval"):
        recovery._require_metadata_approval(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(recovery, "EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256", digest)
    assert recovery._require_metadata_approval(path) == digest
    changed = {**_approval(), "new_provider_health_epoch": True}
    path.write_text(json.dumps(changed))
    monkeypatch.setattr(
        recovery,
        "EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    with pytest.raises(ValueError, match="does not cover this exact stop"):
        recovery._require_metadata_approval(path)


def test_stop_is_exact_and_has_no_cell_193_preflight(tmp_path, monkeypatch) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.write_text("frozen 192-cell stop\n")
    monkeypatch.setattr(
        recovery, "METADATA_STOP_JOURNAL_SHA256", hashlib.sha256(journal.read_bytes()).hexdigest()
    )
    (tmp_path / "pair-postcheck-96.json").write_text("pair receipt\n")
    (tmp_path / "collector-rate-pause.log").write_text("metadata connection reset\n")
    monkeypatch.setattr(
        recovery,
        "PAIR96_SHA256",
        hashlib.sha256((tmp_path / "pair-postcheck-96.json").read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        recovery,
        "STOP_LOG_SHA256",
        hashlib.sha256((tmp_path / "collector-rate-pause.log").read_bytes()).hexdigest(),
    )
    rows = [{"estimated_cost_usd": 0} for _ in range(191)] + [
        {
            "estimated_cost_usd": str(recovery.STOP_COST),
            "cell_id": recovery.FAILED_CELL_ID,
            "run_id": recovery.FAILED_RUN_ID,
            "status": "provider_failed",
            "failure_reason": "provider_unavailable",
            "score_valid": False,
        }
    ]
    monkeypatch.setattr(recovery, "_journal_records", lambda _manifest, _raw: rows)
    assert recovery._require_stop({}, journal) == (rows, recovery.STOP_COST)
    (tmp_path / "endpoint-preflight-97.json").write_text("{}\n")
    with pytest.raises(ValueError, match="extra work"):
        recovery._require_stop({}, journal)


def _clearance_case(tmp_path, monkeypatch) -> tuple[dict, dict, Path]:
    manifest = _manifest()
    health = collector._health_module(ROOT)
    history = tuple(
        [health.CellTerminal("completed")] * 185
        + [health.CellTerminal("provider_failed", "provider_rate_limited")] * 3
        + [health.CellTerminal("completed")] * 3
        + [health.CellTerminal("provider_failed", "provider_unavailable")]
    )
    assert health.provider_health(history) == "pause"
    assert health.provider_health(history[188:]) == "continue"
    journal = tmp_path / "journal.jsonl"
    journal.write_text("exact stopped journal\n")
    stop_sha = hashlib.sha256(journal.read_bytes()).hexdigest()
    monkeypatch.setattr(collector, "METADATA_STOP_JOURNAL_SHA256", stop_sha)
    prior = tmp_path / collector.RATE_PAUSE_CLEARANCE_NAME
    prior.write_text("prior rate clearance\n")
    prior_sha = hashlib.sha256(prior.read_bytes()).hexdigest()
    monkeypatch.setattr(collector, "PRIOR_RATE_CLEARANCE_SHA256", prior_sha)
    pair = tmp_path / "pair-postcheck-96.json"
    pair.write_text("pair 96 receipt\n")
    approval_path = tmp_path / "approval.json"
    approval_path.write_text("{}\n")
    metadata_approval = tmp_path / "metadata-approval.json"
    metadata_approval.write_text(json.dumps(_approval()))
    metadata_sha = hashlib.sha256(metadata_approval.read_bytes()).hexdigest()
    endpoint = manifest["expected_selected_endpoint"]
    selected = {
        "endpoint": f"{endpoint['upstream_provider']} | {endpoint['revision']}",
        "model_id": manifest["model_request"]["name"],
        "provider_name": endpoint["upstream_provider"],
        "status": 0,
        "input_usd_per_million": "3.000000",
        "output_usd_per_million": "15.000000",
    }
    (tmp_path / "endpoint-preflight-96.json").write_text(json.dumps(selected))
    failed = manifest["cells"][191]
    completed = {cell["cell_id"]: {} for cell in manifest["cells"][:191]}
    completed[failed["cell_id"]] = {
        "run_id": recovery.FAILED_RUN_ID,
        "status": "provider_failed",
        "failure_reason": "provider_unavailable",
        "score_valid": False,
    }
    receipt = {
        "protocol": recovery.PROTOCOL,
        "metadata_approval_sha256": metadata_sha,
        "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "original_approval_sha256": hashlib.sha256(approval_path.read_bytes()).hexdigest(),
        "stop_journal_sha256": stop_sha,
        "stop_log_sha256": recovery.STOP_LOG_SHA256,
        "pair96_receipt_sha256": hashlib.sha256(pair.read_bytes()).hexdigest(),
        "prior_rate_clearance_sha256": prior_sha,
        "last_failed_cell_id": failed["cell_id"],
        "last_failed_run_id": recovery.FAILED_RUN_ID,
        "resume_at_order": 193,
        "provider_health_epoch_start_order": 189,
        "new_provider_health_epoch": False,
        "selected_endpoint_recheck": selected,
    }
    clearance = tmp_path / collector.METADATA_RECOVERY_CLEARANCE_NAME
    clearance.write_text(json.dumps(receipt))
    args = {
        "health": health,
        "history": history,
        "completed_pairs": 96,
        "cells": manifest["cells"],
        "completed": completed,
        "manifest": manifest,
        "approval_path": approval_path,
        "journal_path": journal,
        "clearance_path": clearance,
    }
    monkeypatch.setattr(collector, "EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256", metadata_sha)
    return args, receipt, clearance


def test_clearance_preserves_existing_health_epoch(tmp_path, monkeypatch) -> None:
    args, receipt, clearance = _clearance_case(tmp_path, monkeypatch)
    assert collector._admit_provider_pause_clearance(**args) == args["history"][188:]
    clearance.write_text(json.dumps({**receipt, "new_provider_health_epoch": True}))
    with pytest.raises(ValueError, match="differs from frozen evidence"):
        collector._admit_provider_pause_clearance(**args)


def test_clearance_rejects_extra_work_or_zero_approval(tmp_path, monkeypatch) -> None:
    args, _, _ = _clearance_case(tmp_path, monkeypatch)
    (tmp_path / "endpoint-preflight-97.json").write_text("{}\n")
    with pytest.raises(ValueError, match="reconciled boundary"):
        collector._admit_provider_pause_clearance(**args)
    (tmp_path / "endpoint-preflight-97.json").unlink()
    monkeypatch.setattr(collector, "EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="reconciled boundary"):
        collector._admit_provider_pause_clearance(**args)
