"""A provider-health pause can clear only at the audited 90-cell boundary."""

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
import m66_resume_after_provider_pause as recovery  # noqa: E402


def _manifest() -> dict:
    return json.loads((ROOT / "experiments/manifests/m66-confirmatory-v2.json").read_bytes())


def test_separate_paid_approval_is_closed_by_default(tmp_path) -> None:
    approval = tmp_path / "pause-approval.json"
    approval.write_text("{}")
    with pytest.raises(ValueError, match="lacks exact separate approval"):
        recovery._require_pause_approval(approval)


def test_retrospective_pair_receipt_uses_frozen_v2_protocol() -> None:
    receipt = recovery._retrospective_pair_receipt(
        audits=[{"cell_id": "first"}, {"cell_id": "second"}],
        pair_preflight_sha256="a" * 64,
        cumulative_cost=Decimal("39.286650"),
    )
    assert receipt["protocol"] == _manifest()["protocol"] == "m66-confirmatory-v2"
    assert receipt["pair_number"] == 45
    assert receipt["cell_ids"] == list(recovery.FAILED_CELL_IDS)
    assert receipt["run_ids"] == list(recovery.FAILED_RUN_IDS)
    assert receipt["retrospective_provider_pause_closure"] is True
    with pytest.raises(ValueError, match="both authoritative audits"):
        recovery._retrospective_pair_receipt(
            audits=[{"cell_id": "first"}],
            pair_preflight_sha256="a" * 64,
            cumulative_cost=Decimal("39.286650"),
        )


def _clearance_case(tmp_path, monkeypatch):
    manifest = _manifest()
    cells = manifest["cells"][:90]
    completed = {cell["cell_id"]: {"run_id": f"run-{order}"} for order, cell in enumerate(cells, 1)}
    for cell in cells[-2:]:
        completed[cell["cell_id"]].update(
            {
                "status": "provider_failed",
                "failure_reason": "provider_unavailable",
                "score_valid": False,
            }
        )
    health = collector._health_module(ROOT)
    history = tuple(
        [health.CellTerminal("completed")] * 88
        + [health.CellTerminal("provider_failed", "provider_unavailable")] * 2
    )
    journal = tmp_path / "journal.jsonl"
    journal.write_text("frozen stop\n")
    stop_sha = hashlib.sha256(journal.read_bytes()).hexdigest()
    monkeypatch.setattr(collector, "PROVIDER_PAUSE_STOP_JOURNAL_SHA256", stop_sha)
    monkeypatch.setattr(collector, "EXPECTED_PAUSE_APPROVAL_SHA256", "a" * 64)
    pair = tmp_path / "pair-postcheck-45.json"
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
    receipt = {
        "protocol": "m66-confirmatory-v2-provider-pause-clearance-v1",
        "pause_approval_sha256": "a" * 64,
        "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "approval_sha256": hashlib.sha256(approval.read_bytes()).hexdigest(),
        "stop_journal_sha256": stop_sha,
        "pair45_receipt_sha256": hashlib.sha256(pair.read_bytes()).hexdigest(),
        "provider_health_before": "pause",
        "provider_health_epoch_start_order": 91,
        "failed_cell_ids": [cell["cell_id"] for cell in cells[-2:]],
        "failed_run_ids": [completed[cell["cell_id"]]["run_id"] for cell in cells[-2:]],
        "selected_endpoint_recheck": selected,
    }
    clearance = tmp_path / "provider-pause-clearance-after-cell-90.json"
    clearance.write_text(json.dumps(receipt))
    args = {
        "health": health,
        "history": history,
        "completed_pairs": 45,
        "cells": manifest["cells"],
        "completed": completed,
        "manifest": manifest,
        "approval_path": approval,
        "journal_path": journal,
        "clearance_path": clearance,
    }
    return args, receipt, clearance


def test_pause_clearance_resets_only_the_future_health_epoch(tmp_path, monkeypatch) -> None:
    args, receipt, clearance = _clearance_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="provider-health gate"):
        collector._admit_provider_pause_clearance(**{**args, "clearance_path": None})
    assert collector._admit_provider_pause_clearance(**args) == ()
    assert args["history"][-2:] == (
        args["health"].CellTerminal("provider_failed", "provider_unavailable"),
        args["health"].CellTerminal("provider_failed", "provider_unavailable"),
    )
    clearance.write_text(json.dumps({**receipt, "provider_health_epoch_start_order": 90}))
    with pytest.raises(ValueError, match="differs from frozen evidence"):
        collector._admit_provider_pause_clearance(**args)


def test_clearance_rejects_extra_work_or_changed_price(tmp_path, monkeypatch) -> None:
    args, receipt, clearance = _clearance_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="reconciled boundary"):
        collector._admit_provider_pause_clearance(**{**args, "completed_pairs": 46})
    receipt["selected_endpoint_recheck"]["output_usd_per_million"] = "16"
    clearance.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="differs from frozen evidence"):
        collector._admit_provider_pause_clearance(**args)


def test_stop_snapshot_cannot_gain_an_extra_cell(tmp_path, monkeypatch) -> None:
    manifest = _manifest()
    endpoint = manifest["expected_selected_endpoint"]
    journal = tmp_path / "journal.jsonl"
    rows = [
        {
            "type": "batch_started",
            "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
            "expected_model_revision": endpoint["revision"],
            "expected_upstream_provider": endpoint["upstream_provider"],
            "max_estimated_usd": 504.0,
        }
    ]
    for order, cell in enumerate(manifest["cells"][:90], 1):
        rows.append({"type": "cell_started", "cell_id": cell["cell_id"], "order": order})
        terminal = {
            "type": "cell_completed",
            "cell_id": cell["cell_id"],
            "order": order,
            "run_id": f"run-{order}",
            "estimated_cost_usd": 0.0,
        }
        if order in (89, 90):
            terminal.update(
                {
                    "run_id": recovery.FAILED_RUN_IDS[order - 89],
                    "status": "provider_failed",
                    "failure_reason": "provider_unavailable",
                    "score_valid": False,
                }
            )
        rows.append(terminal)
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(
        recovery, "STOP_JOURNAL_SHA256", hashlib.sha256(journal.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(recovery, "STOP_COST", Decimal("0"))
    assert len(recovery._require_stop(manifest, journal)[0]) == 90
    journal.write_bytes(journal.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="audited stop"):
        recovery._require_stop(manifest, journal)
