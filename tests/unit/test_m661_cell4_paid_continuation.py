"""Fail-closed checks for the separate cell-four paid continuation proposal."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m661_cell4_recovery_postcheck as postcheck  # noqa: E402
import m661_cell4_repaired_stop as repaired  # noqa: E402
import m661_resume_after_cell4_permission as continuation  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads((ROOT / "experiments/manifests/m661-feasibility-v1.json").read_bytes())


def test_paid_continuation_closed_before_receipt_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert continuation.EXPECTED_PAID_APPROVAL_SHA256 != "0" * 64
    assert continuation.OPERATIONAL_MERGE_COMMIT == "41b77717e6f059821fb9149f90c886afed87b304"
    assert continuation.REPAIR_COMPLETION_SHA256 == repaired.COMPLETION_SHA256
    monkeypatch.setattr(continuation, "EXPECTED_PAID_APPROVAL_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="remains closed"):
        continuation._scope_approval(tmp_path / "cell4-paid-continuation-approval.json", MANIFEST)


def test_paid_scope_binds_both_missing_runs_and_exact_new_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cell4-paid-continuation-approval.json"
    receipt = {
        "approved_at": "2026-10-12T14:00:00Z",
        "approved_by": "human_user",
        "approved_for_paid_calls": True,
        "approval_scope": continuation.CONTINUATION_SCOPE,
        "protocol": MANIFEST["protocol"],
        "manifest_sha256": continuation.EXPECTED_MANIFEST_SHA256,
        "source_commit": continuation.SOURCE_COMMIT,
        "protocol_commit": continuation.PROTOCOL_COMMIT,
        "operational_merge_commit": continuation.OPERATIONAL_MERGE_COMMIT,
        "repair_completion_sha256": continuation.REPAIR_COMPLETION_SHA256,
        "assignment_sha256": MANIFEST["schedule"]["cell_assignment_sha256"],
        "original_journal_sha256": continuation.STOP_JOURNAL_SHA256,
        "stopped_continuation_journal_sha256": continuation.CONTINUATION_JOURNAL_SHA256,
        "stopped_continuation_log_sha256": continuation.CONTINUATION_LOG_SHA256,
        "retained_cell1_run_id": str(continuation.STOP_RUN_ID),
        "retained_cell4_run_id": str(continuation.CELL4_RUN_ID),
        "first_paid_cell_order": 5,
        "last_paid_cell_order": 96,
        "paid_cells_authorized": 92,
        "retained_missing_orders": [1, 4],
        "no_retry_or_replacement": True,
        "cumulative_cost_ceiling_usd": str(continuation.CAP),
    }

    def write() -> None:
        raw = json.dumps(receipt, sort_keys=True).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        monkeypatch.setattr(
            continuation, "EXPECTED_PAID_APPROVAL_SHA256", hashlib.sha256(raw).hexdigest()
        )

    write()
    assert continuation._scope_approval(path, MANIFEST) == receipt
    for key, value in (
        ("first_paid_cell_order", 4),
        ("paid_cells_authorized", 93),
        ("retained_missing_orders", [1]),
        ("stopped_continuation_journal_sha256", "0" * 64),
        ("approved_for_paid_calls", False),
        ("operational_merge_commit", "0" * 40),
        ("repair_completion_sha256", "0" * 64),
    ):
        original = receipt[key]
        receipt[key] = value
        write()
        with pytest.raises(ValueError, match="exact stopped scope"):
            continuation._scope_approval(path, MANIFEST)
        receipt[key] = original


def test_new_journal_requires_frozen_order_five(tmp_path: Path) -> None:
    path = tmp_path / "cell4-paid-continuation-journal.jsonl"
    header = {
        "type": "batch_started",
        "manifest_sha256": continuation.EXPECTED_MANIFEST_SHA256,
        "expected_model_revision": continuation.REVISION,
        "expected_upstream_provider": continuation.UPSTREAM,
        "max_estimated_usd": float(continuation.CAP),
    }
    first = MANIFEST["cells"][4]
    start = {"type": "cell_started", "order": 5, "cell_id": first["cell_id"], "run_id": "new-run"}
    done = {
        "type": "cell_completed",
        "order": 5,
        "cell_id": first["cell_id"],
        "run_id": "new-run",
        "experiment_sha256": first["experiment_sha256"],
    }
    path.write_text("\n".join(json.dumps(item) for item in (header, start, done)) + "\n")
    assert len(postcheck._new_records(MANIFEST, path)) == 1
    start["order"] = 4
    path.write_text("\n".join(json.dumps(item) for item in (header, start, done)) + "\n")
    with pytest.raises(ValueError, match="frozen order"):
        postcheck._new_records(MANIFEST, path)


def test_retained_four_pair_audit_cannot_be_score_valid() -> None:
    audit = repaired.retained_four_pair_audit(MANIFEST["cells"][3])
    assert audit["run_id"] == str(continuation.CELL4_RUN_ID)
    assert audit["score_valid"] is False
    assert audit["model_calls"] == 0
    assert audit["trace_auditable"] is False
