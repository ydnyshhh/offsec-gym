"""The cell-89 recovery gate must fail closed on any changed evidence."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m66_resume_after_provider_failure as recovery  # noqa: E402


def _checkpoint(tmp_path, monkeypatch):
    manifest = json.loads((ROOT / "experiments/manifests/m66-confirmatory-v2.json").read_bytes())
    journal = tmp_path / "journal.jsonl"
    rows = [{"type": "batch_started"}]
    for order, cell in enumerate(manifest["cells"][:89], 1):
        rows.append({"type": "cell_started", "cell_id": cell["cell_id"], "order": order})
        terminal = {"type": "cell_completed", "cell_id": cell["cell_id"], "order": order}
        if order == 89:
            terminal.update(
                {
                    "run_id": recovery.STOP_RUN_ID,
                    "trace_sha256": recovery.STOP_TRACE_SHA256,
                    "status": "provider_failed",
                    "failure_reason": "provider_unavailable",
                    "score_valid": False,
                }
            )
        rows.append(terminal)
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    digest = hashlib.sha256(journal.read_bytes()).hexdigest()
    monkeypatch.setattr(recovery, "STOP_JOURNAL_SHA256", digest)
    result = {
        "journal_sha256": digest,
        "manifest_sha256": recovery.EXPECTED_MANIFEST_SHA256,
        "journaled_cells": 89,
        "score_valid_cells": 88,
        "provider_failed_cells": 1,
        "estimated_model_token_cost_usd": str(recovery.STOP_COST),
        "cells": {cell["cell_id"]: {} for cell in manifest["cells"][:89]},
    }
    return manifest, journal, result


def test_recovery_accepts_only_exact_audited_partial_pair(tmp_path, monkeypatch) -> None:
    manifest, journal, result = _checkpoint(tmp_path, monkeypatch)
    first, second = recovery._require_stopped_prefix(manifest, journal, result)
    assert first["cell_id"] == recovery.STOP_CELL_ID
    assert second["cell_id"] == manifest["cells"][89]["cell_id"]


def test_recovery_rejects_changed_journal_and_extra_paid_work(tmp_path, monkeypatch) -> None:
    manifest, journal, result = _checkpoint(tmp_path, monkeypatch)
    journal.write_bytes(journal.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="reconciled checkpoint"):
        recovery._require_stopped_prefix(manifest, journal, result)

    manifest, journal, result = _checkpoint(tmp_path, monkeypatch)
    result["cells"][manifest["cells"][89]["cell_id"]] = {}
    with pytest.raises(ValueError, match="unstarted partner"):
        recovery._require_stopped_prefix(manifest, journal, result)


def test_recovery_rejects_changed_failure_and_existing_receipt(tmp_path, monkeypatch) -> None:
    manifest, journal, result = _checkpoint(tmp_path, monkeypatch)
    (tmp_path / "pair-postcheck-45.json").write_text("{}")
    with pytest.raises(ValueError, match="unstarted partner"):
        recovery._require_stopped_prefix(manifest, journal, result)

    (tmp_path / "pair-postcheck-45.json").unlink()
    (tmp_path / "endpoint-recheck-cell-89.json").write_text("{}")
    with pytest.raises(ValueError, match="unstarted partner"):
        recovery._require_stopped_prefix(manifest, journal, result)
