"""The partial pair-125 transport stop needs an exact, separate clearance."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m66_confirmatory_collect as collector  # noqa: E402
import m66_resume_after_cell249_metadata as recovery  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest() -> dict:
    return json.loads((ROOT / "experiments/manifests/m66-confirmatory-v2.json").read_bytes())


def _approval() -> dict:
    return {
        "protocol": recovery.PROTOCOL,
        "approved_for_paid_calls": True,
        "approval_scope": "assigned_cells_250_through_280_only_no_retry_or_replacement",
        "original_manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "original_approval_sha256": collector.EXPECTED_APPROVAL_SHA256,
        "prior_cell227_approval_sha256": collector.EXPECTED_CELL227_PAUSE_APPROVAL_SHA256,
        "stop_journal_sha256": collector.CELL249_METADATA_STOP_JOURNAL_SHA256,
        "provider_health_epoch_start_order": 228,
        "new_provider_health_epoch": False,
        "retained_invalid_cell_orders": recovery.RETAINED_INVALID_ORDERS,
        "retained_agent_failed_cell_order": 162,
        "retained_score_valid_cell_order": 208,
        "cost_ceiling_usd": 504,
    }


def test_paid_continuation_requires_exact_new_approval(tmp_path, monkeypatch) -> None:
    path = tmp_path / "cell249-approval.json"
    path.write_text(json.dumps(_approval()))
    assert collector.EXPECTED_CELL249_METADATA_APPROVAL_SHA256 == (
        "073ed88c330ab88f16fa88a650261b8a35a45a84a8662ec04d6f9257bbe1cb47"
    )
    monkeypatch.setattr(recovery, "EXPECTED_CELL249_METADATA_APPROVAL_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="lacks separate exact paid-call approval"):
        recovery._require_approval(path)
    monkeypatch.setattr(
        recovery, "EXPECTED_CELL249_METADATA_APPROVAL_SHA256", _sha(path.read_bytes())
    )
    assert recovery._require_approval(path) == _sha(path.read_bytes())
    changed = {**_approval(), "new_provider_health_epoch": True}
    path.write_text(json.dumps(changed))
    monkeypatch.setattr(
        recovery, "EXPECTED_CELL249_METADATA_APPROVAL_SHA256", _sha(path.read_bytes())
    )
    with pytest.raises(ValueError, match="does not cover this exact stop"):
        recovery._require_approval(path)


def _clearance_case(tmp_path, monkeypatch):
    manifest = _manifest()
    cells = manifest["cells"]
    health = collector._health_module(ROOT)
    history = tuple(
        [health.CellTerminal("completed")] * 248
        + [health.CellTerminal("provider_failed", "provider_unavailable")]
        + [health.CellTerminal("completed")]
    )
    assert health.provider_health(history[227:]) == "continue"
    completed = {
        cell["cell_id"]: {"run_id": f"run-{order}"} for order, cell in enumerate(cells[:250], 1)
    }
    completed[cells[248]["cell_id"]] = {
        "run_id": recovery.FAILED_RUN_ID,
        "status": "provider_failed",
        "failure_reason": "provider_unavailable",
        "score_valid": False,
    }
    journal = tmp_path / "journal.jsonl"
    rows = [{"type": "batch_started"}]
    for order, cell in enumerate(cells[:250], 1):
        rows.extend(
            (
                {"type": "cell_started", "cell_id": cell["cell_id"], "order": order},
                {"type": "cell_completed", "cell_id": cell["cell_id"], "order": order},
            )
        )
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    stop = b"".join(journal.read_bytes().splitlines(keepends=True)[:499])
    monkeypatch.setattr(collector, "CELL249_METADATA_STOP_JOURNAL_SHA256", _sha(stop))
    original_approval = tmp_path / "approval.json"
    original_approval.write_text("{}\n")
    new_approval = tmp_path / "cell249-approval.json"
    approved = _approval()
    approved["stop_journal_sha256"] = _sha(stop)
    new_approval.write_text(json.dumps(approved))
    monkeypatch.setattr(
        collector, "EXPECTED_CELL249_METADATA_APPROVAL_SHA256", _sha(new_approval.read_bytes())
    )
    log = tmp_path / "collector-cell227.log"
    log.write_text("stopped after metadata TLS EOF\n")
    monkeypatch.setattr(collector, "CELL249_METADATA_STOP_LOG_SHA256", _sha(log.read_bytes()))
    pair124 = tmp_path / "pair-postcheck-124.json"
    pair124.write_text("{}\n")
    monkeypatch.setattr(collector, "CELL249_PAIR124_SHA256", _sha(pair124.read_bytes()))
    prior = tmp_path / collector.CELL227_PAUSE_CLEARANCE_NAME
    prior.write_text("{}\n")
    monkeypatch.setattr(collector, "PRIOR_CELL227_CLEARANCE_SHA256", _sha(prior.read_bytes()))
    endpoint = manifest["expected_selected_endpoint"]
    selected = {
        "endpoint": f"{endpoint['upstream_provider']} | {endpoint['revision']}",
        "model_id": manifest["model_request"]["name"],
        "provider_name": endpoint["upstream_provider"],
        "status": 0,
        "input_usd_per_million": "3.000000",
        "output_usd_per_million": "15.000000",
    }
    original_endpoint = tmp_path / "endpoint-preflight-125.json"
    original_endpoint.write_text(json.dumps(selected))
    monkeypatch.setattr(
        collector, "CELL249_ENDPOINT125_SHA256", _sha(original_endpoint.read_bytes())
    )
    recheck = tmp_path / "endpoint-recheck-cell-249.json"
    recheck.write_text(json.dumps(selected))
    (tmp_path / "pair-postcheck-125.json").write_text("{}\n")
    receipt = {
        "protocol": recovery.PROTOCOL,
        "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "original_approval_sha256": _sha(original_approval.read_bytes()),
        "cell249_approval_sha256": _sha(new_approval.read_bytes()),
        "stop_journal_sha256": _sha(stop),
        "stop_log_sha256": _sha(log.read_bytes()),
        "pair124_receipt_sha256": _sha(pair124.read_bytes()),
        "endpoint_preflight_125_sha256": _sha(original_endpoint.read_bytes()),
        "prior_cell227_clearance_sha256": _sha(prior.read_bytes()),
        "endpoint_recheck_cell249_sha256": _sha(recheck.read_bytes()),
        "selected_endpoint_recheck": selected,
        "last_failed_cell_id": cells[248]["cell_id"],
        "last_failed_run_id": recovery.FAILED_RUN_ID,
        "resume_at_order": 250,
        "provider_health_epoch_start_order": 228,
        "new_provider_health_epoch": False,
    }
    clearance = tmp_path / collector.CELL249_METADATA_CLEARANCE_NAME
    clearance.write_text(json.dumps(receipt))
    args = {
        "health": health,
        "history": history,
        "completed_pairs": 125,
        "cells": cells,
        "completed": completed,
        "manifest": manifest,
        "approval_path": original_approval,
        "journal_path": journal,
        "clearance_path": clearance,
    }
    return args, receipt, clearance


def test_clearance_preserves_cell_228_health_epoch(tmp_path, monkeypatch) -> None:
    args, receipt, clearance = _clearance_case(tmp_path, monkeypatch)
    assert collector._admit_provider_pause_clearance(**args) == args["history"][227:]
    clearance.write_text(json.dumps({**receipt, "new_provider_health_epoch": True}))
    with pytest.raises(ValueError, match="provenance differs"):
        collector._admit_provider_pause_clearance(**args)


def test_clearance_rejects_price_or_stop_log_tampering(tmp_path, monkeypatch) -> None:
    args, receipt, clearance = _clearance_case(tmp_path, monkeypatch)
    recheck = tmp_path / "endpoint-recheck-cell-249.json"
    changed = {**receipt["selected_endpoint_recheck"], "output_usd_per_million": "16.000000"}
    recheck.write_text(json.dumps(changed))
    receipt["endpoint_recheck_cell249_sha256"] = _sha(recheck.read_bytes())
    receipt["selected_endpoint_recheck"] = changed
    clearance.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="provenance differs"):
        collector._admit_provider_pause_clearance(**args)
    (tmp_path / "collector-cell227.log").write_text("changed\n")
    with pytest.raises(ValueError, match="reviewed boundary"):
        collector._admit_provider_pause_clearance(**args)
