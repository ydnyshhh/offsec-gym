"""The second provider-health epoch is exact, separate, and fail closed."""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m66_confirmatory_collect as collector  # noqa: E402
import m66_resume_after_rate_pause as recovery  # noqa: E402


def _manifest() -> dict:
    return json.loads((ROOT / "experiments/manifests/m66-confirmatory-v2.json").read_bytes())


def test_paid_rate_continuation_requires_new_exact_approval(tmp_path, monkeypatch) -> None:
    path = tmp_path / "rate-approval.json"
    with pytest.raises(ValueError, match="lacks separate exact approval"):
        recovery._require_rate_approval(path)
    receipt = {
        "protocol": "m66-confirmatory-v2-rate-pause-clearance-v1",
        "approved_for_paid_calls": True,
        "approval_scope": "assigned_cells_189_through_280_only_no_retry_or_replacement",
        "original_manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "original_approval_sha256": collector.EXPECTED_APPROVAL_SHA256,
        "prior_pause_approval_sha256": collector.EXPECTED_PAUSE_APPROVAL_SHA256,
        "prior_stage_approval_sha256": recovery.EXPECTED_STAGE_APPROVAL_SHA256,
        "stop_journal_sha256": recovery.RATE_PAUSE_STOP_JOURNAL_SHA256,
        "cost_ceiling_usd": 504.0,
        "provider_health_reset_after_order": 188,
        "retained_invalid_cell_orders": recovery.RETAINED_INVALID_ORDERS,
        "retained_agent_failed_cell_order": 162,
    }
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="lacks separate exact approval"):
        recovery._require_rate_approval(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(recovery, "EXPECTED_RATE_PAUSE_APPROVAL_SHA256", digest)
    assert recovery._require_rate_approval(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    receipt["retained_invalid_cell_orders"] = [89, 90]
    path.write_text(json.dumps(receipt))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(recovery, "EXPECTED_RATE_PAUSE_APPROVAL_SHA256", digest)
    with pytest.raises(ValueError, match="does not cover this exact stop"):
        recovery._require_rate_approval(path)


def test_stop_is_exact_188_cell_prefix(tmp_path, monkeypatch) -> None:
    manifest = _manifest()
    endpoint = manifest["expected_selected_endpoint"]
    rows = [
        {
            "type": "batch_started",
            "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
            "expected_model_revision": endpoint["revision"],
            "expected_upstream_provider": endpoint["upstream_provider"],
            "max_estimated_usd": 504.0,
        }
    ]
    for order, cell in enumerate(manifest["cells"][:188], 1):
        rows.append({"type": "cell_started", "cell_id": cell["cell_id"], "order": order})
        terminal = {
            "type": "cell_completed",
            "cell_id": cell["cell_id"],
            "order": order,
            "run_id": f"run-{order}",
            "estimated_cost_usd": 0,
        }
        if order in (186, 187, 188):
            terminal.update(
                {
                    "run_id": recovery.FAILED_RUN_IDS[order - 186],
                    "status": "provider_failed",
                    "failure_reason": "provider_rate_limited",
                    "score_valid": False,
                }
            )
        rows.append(terminal)
    journal = tmp_path / "journal.jsonl"
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(
        recovery, "RATE_PAUSE_STOP_JOURNAL_SHA256", hashlib.sha256(journal.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(recovery, "STOP_COST", Decimal(0))
    assert len(recovery._require_stop(manifest, journal)[0]) == 188
    (tmp_path / "pair-postcheck-94.json").write_text("{}")
    with pytest.raises(ValueError, match="extra work"):
        recovery._require_stop(manifest, journal)


def test_retrospective_pair94_preserves_both_retained_failures() -> None:
    receipt = recovery._retrospective_pair_receipt(
        audits=[{"cell_id": recovery.FAILED_CELL_IDS[1]}, {"cell_id": recovery.FAILED_CELL_IDS[2]}],
        pair_preflight_sha256="a" * 64,
        cumulative_cost=Decimal("82.502808"),
    )
    assert receipt["pair_number"] == 94
    assert receipt["cell_ids"] == list(recovery.FAILED_CELL_IDS[1:])
    assert receipt["run_ids"] == list(recovery.FAILED_RUN_IDS[1:])
    assert receipt["retrospective_rate_pause_closure"] is True
    with pytest.raises(ValueError, match="both authoritative audits"):
        recovery._retrospective_pair_receipt(
            audits=[{"cell_id": recovery.FAILED_CELL_IDS[1]}],
            pair_preflight_sha256="a" * 64,
            cumulative_cost=Decimal("82.502808"),
        )


def _clearance_case(tmp_path, monkeypatch):
    manifest = _manifest()
    cells = manifest["cells"][:188]
    completed = {cell["cell_id"]: {"run_id": f"run-{order}"} for order, cell in enumerate(cells, 1)}
    for cell in cells[-3:]:
        completed[cell["cell_id"]].update(
            {
                "status": "provider_failed",
                "failure_reason": "provider_rate_limited",
                "score_valid": False,
            }
        )
    health = collector._health_module(ROOT)
    history = tuple(
        [health.CellTerminal("completed")] * 185
        + [health.CellTerminal("provider_failed", "provider_rate_limited")] * 3
    )
    journal = tmp_path / "journal.jsonl"
    journal.write_text("exact stopped journal\n")
    stop_sha = hashlib.sha256(journal.read_bytes()).hexdigest()
    monkeypatch.setattr(collector, "RATE_PAUSE_STOP_JOURNAL_SHA256", stop_sha)
    monkeypatch.setattr(collector, "EXPECTED_RATE_PAUSE_APPROVAL_SHA256", "a" * 64)
    pair = tmp_path / "pair-postcheck-94.json"
    pair.write_text("{}\n")
    approval = tmp_path / "approval.json"
    approval.write_text("{}\n")
    endpoint = manifest["expected_selected_endpoint"]
    selected = {
        "endpoint": f"{endpoint['upstream_provider']} | {endpoint['revision']}",
        "model_id": manifest["model_request"]["name"],
        "provider_name": endpoint["upstream_provider"],
        "status": 0,
        "input_usd_per_million": "3.000000",
        "output_usd_per_million": "15.000000",
    }
    (tmp_path / "endpoint-preflight-94.json").write_text(json.dumps(selected))
    (tmp_path / "endpoint-recheck-cell-187.json").write_text(json.dumps(selected))
    receipt = {
        "protocol": "m66-confirmatory-v2-rate-pause-clearance-v1",
        "rate_pause_approval_sha256": "a" * 64,
        "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "approval_sha256": hashlib.sha256(approval.read_bytes()).hexdigest(),
        "stop_journal_sha256": stop_sha,
        "pair94_receipt_sha256": hashlib.sha256(pair.read_bytes()).hexdigest(),
        "provider_health_before": "pause",
        "provider_health_epoch_start_order": 189,
        "failed_cell_ids": [cell["cell_id"] for cell in cells[-3:]],
        "failed_run_ids": [completed[cell["cell_id"]]["run_id"] for cell in cells[-3:]],
        "selected_endpoint_recheck": selected,
    }
    clearance = tmp_path / collector.RATE_PAUSE_CLEARANCE_NAME
    clearance.write_text(json.dumps(receipt))
    args = {
        "health": health,
        "history": history,
        "completed_pairs": 94,
        "cells": manifest["cells"],
        "completed": completed,
        "manifest": manifest,
        "approval_path": approval,
        "journal_path": journal,
        "clearance_path": clearance,
    }
    return args, receipt, clearance


def test_rate_pause_cannot_clear_without_its_exact_receipt(tmp_path, monkeypatch) -> None:
    args, receipt, clearance = _clearance_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="provider-health gate"):
        collector._admit_provider_pause_clearance(**{**args, "clearance_path": None})
    monkeypatch.setattr(collector, "EXPECTED_RATE_PAUSE_APPROVAL_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="reconciled boundary"):
        collector._admit_provider_pause_clearance(**args)
    monkeypatch.setattr(collector, "EXPECTED_RATE_PAUSE_APPROVAL_SHA256", "a" * 64)
    assert collector._admit_provider_pause_clearance(**args) == ()
    clearance.write_text(json.dumps({**receipt, "provider_health_epoch_start_order": 188}))
    with pytest.raises(ValueError, match="differs from frozen evidence"):
        collector._admit_provider_pause_clearance(**args)


def test_rate_pause_rejects_extra_work_and_price_drift(tmp_path, monkeypatch) -> None:
    args, receipt, clearance = _clearance_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="reconciled boundary"):
        collector._admit_provider_pause_clearance(**{**args, "completed_pairs": 95})
    receipt["selected_endpoint_recheck"]["output_usd_per_million"] = "16"
    clearance.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="differs from frozen evidence"):
        collector._admit_provider_pause_clearance(**args)
