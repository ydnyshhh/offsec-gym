"""The partial-pair provider pause remains sealed until its exact clearance."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m66_confirmatory_collect as collector  # noqa: E402
import m66_resume_after_cell227_pause as recovery  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest() -> dict:
    return json.loads((ROOT / "experiments/manifests/m66-confirmatory-v2.json").read_bytes())


def _approval() -> dict:
    return {
        "protocol": recovery.PROTOCOL,
        "approved_for_paid_calls": True,
        "approval_scope": "assigned_cells_228_through_280_only_no_retry_or_replacement",
        "original_manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "original_approval_sha256": collector.EXPECTED_APPROVAL_SHA256,
        "prior_metadata_approval_sha256": collector.EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256,
        "prior_cell208_approval_sha256": collector.PRIOR_CELL208_APPROVAL_SHA256,
        "stop_journal_sha256": collector.CELL227_PAUSE_STOP_JOURNAL_SHA256,
        "provider_health_epoch_start_order": 228,
        "new_provider_health_epoch": True,
        "retained_invalid_cell_orders": recovery.RETAINED_INVALID_ORDERS,
        "retained_agent_failed_cell_order": 162,
        "retained_score_valid_cell_order": 208,
        "cost_ceiling_usd": 504,
    }


def test_new_paid_calls_require_exact_scope_and_pinned_receipt(tmp_path, monkeypatch) -> None:
    path = tmp_path / "cell227-approval.json"
    path.write_text(json.dumps(_approval()))
    with pytest.raises(ValueError, match="lacks separate exact paid-call approval"):
        recovery._require_approval(path)
    monkeypatch.setattr(recovery, "EXPECTED_CELL227_PAUSE_APPROVAL_SHA256", _sha(path.read_bytes()))
    assert recovery._require_approval(path) == _sha(path.read_bytes())
    changed = {**_approval(), "provider_health_epoch_start_order": 229}
    path.write_text(json.dumps(changed))
    monkeypatch.setattr(recovery, "EXPECTED_CELL227_PAUSE_APPROVAL_SHA256", _sha(path.read_bytes()))
    with pytest.raises(ValueError, match="does not cover this exact stop"):
        recovery._require_approval(path)


def _clearance_case(tmp_path, monkeypatch):
    manifest = _manifest()
    cells = manifest["cells"]
    health = collector._health_module(ROOT)
    history = tuple(
        [health.CellTerminal("completed")] * 225
        + [health.CellTerminal("provider_failed", "provider_unavailable")] * 2
        + [health.CellTerminal("completed")]
    )
    completed = {
        cell["cell_id"]: {"run_id": f"run-{order}"} for order, cell in enumerate(cells[:228], 1)
    }
    for index, run_id in zip((225, 226), recovery.FAILED_RUN_IDS, strict=True):
        completed[cells[index]["cell_id"]] = {
            "run_id": run_id,
            "status": "provider_failed",
            "failure_reason": "provider_unavailable",
            "score_valid": False,
        }

    journal = tmp_path / "journal.jsonl"
    rows = [{"type": "batch_started"}]
    for order, cell in enumerate(cells[:228], 1):
        rows.extend(
            (
                {"type": "cell_started", "cell_id": cell["cell_id"], "order": order},
                {"type": "cell_completed", "cell_id": cell["cell_id"], "order": order},
            )
        )
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    stop = b"".join(journal.read_bytes().splitlines(keepends=True)[:455])
    monkeypatch.setattr(collector, "CELL227_PAUSE_STOP_JOURNAL_SHA256", _sha(stop))

    original_approval = tmp_path / "approval.json"
    original_approval.write_text("{}\n")
    pause_approval = tmp_path / "cell227-approval.json"
    approved = _approval()
    approved["stop_journal_sha256"] = _sha(stop)
    pause_approval.write_text(json.dumps(approved))
    monkeypatch.setattr(
        collector, "EXPECTED_CELL227_PAUSE_APPROVAL_SHA256", _sha(pause_approval.read_bytes())
    )
    (tmp_path / "collector-cell208-reconciled.log").write_text("stopped\n")
    (tmp_path / "pair-postcheck-113.json").write_text("{}\n")
    (tmp_path / "pair-postcheck-114.json").write_text("{}\n")
    endpoint = manifest["expected_selected_endpoint"]
    selected = {
        "endpoint": f"{endpoint['upstream_provider']} | {endpoint['revision']}",
        "model_id": manifest["model_request"]["name"],
        "provider_name": endpoint["upstream_provider"],
        "status": 0,
        "input_usd_per_million": "3.000000",
        "output_usd_per_million": "15.000000",
    }
    original_endpoint = tmp_path / "endpoint-preflight-114.json"
    original_endpoint.write_text(json.dumps(selected))
    recheck = tmp_path / "endpoint-recheck-cell-227.json"
    recheck.write_text(json.dumps(selected))
    receipt = {
        "protocol": recovery.PROTOCOL,
        "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "original_approval_sha256": _sha(original_approval.read_bytes()),
        "cell227_approval_sha256": _sha(pause_approval.read_bytes()),
        "stop_journal_sha256": _sha(stop),
        "stop_log_sha256": _sha((tmp_path / "collector-cell208-reconciled.log").read_bytes()),
        "pair113_receipt_sha256": _sha((tmp_path / "pair-postcheck-113.json").read_bytes()),
        "endpoint_preflight_114_sha256": _sha(original_endpoint.read_bytes()),
        "endpoint_recheck_cell227_sha256": _sha(recheck.read_bytes()),
        "failed_cell_ids": [cell["cell_id"] for cell in cells[225:227]],
        "failed_run_ids": recovery.FAILED_RUN_IDS,
        "provider_health_before": "pause",
        "provider_health_epoch_start_order": 228,
        "new_provider_health_epoch": True,
        "selected_endpoint_recheck": selected,
    }
    clearance = tmp_path / collector.CELL227_PAUSE_CLEARANCE_NAME
    clearance.write_text(json.dumps(receipt))
    args = {
        "health": health,
        "history": history,
        "completed_pairs": 114,
        "cells": cells,
        "completed": completed,
        "manifest": manifest,
        "approval_path": original_approval,
        "journal_path": journal,
        "clearance_path": clearance,
    }
    return args, receipt, clearance


def test_partial_pair_clearance_preserves_new_epoch_only(tmp_path, monkeypatch) -> None:
    args, receipt, clearance = _clearance_case(tmp_path, monkeypatch)
    assert collector._admit_cell227_pause_clearance(**args) == args["history"][227:]
    with pytest.raises(ValueError, match="reviewed boundary"):
        collector._admit_cell227_pause_clearance(**{**args, "completed_pairs": 113})
    clearance.write_text(json.dumps({**receipt, "provider_health_epoch_start_order": 229}))
    with pytest.raises(ValueError, match="provenance differs"):
        collector._admit_cell227_pause_clearance(**args)


def test_clearance_rejects_endpoint_and_log_tampering(tmp_path, monkeypatch) -> None:
    args, receipt, clearance = _clearance_case(tmp_path, monkeypatch)
    receipt["selected_endpoint_recheck"]["output_usd_per_million"] = "16.000000"
    clearance.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="provenance differs"):
        collector._admit_cell227_pause_clearance(**args)
    receipt["selected_endpoint_recheck"]["output_usd_per_million"] = "15.000000"
    clearance.write_text(json.dumps(receipt))
    (tmp_path / "collector-cell208-reconciled.log").write_text("different\n")
    with pytest.raises(ValueError, match="provenance differs"):
        collector._admit_cell227_pause_clearance(**args)
