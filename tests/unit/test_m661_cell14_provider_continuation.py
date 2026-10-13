"""Fail-closed scope and epoch checks for the proposed cell-14 continuation."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m661_cell4_recovery_postcheck as postcheck  # noqa: E402
import m661_resume_after_cell14_pause as continuation  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads((ROOT / "experiments/manifests/m661-feasibility-v1.json").read_bytes())


def _approval() -> dict:
    return {
        "approved_at": "2026-10-13T00:00:00Z",
        "approved_by": "human_user",
        "approved_for_paid_calls": True,
        "approval_scope": continuation.CONTINUATION_SCOPE,
        "protocol": MANIFEST["protocol"],
        "manifest_sha256": continuation.EXPECTED_MANIFEST_SHA256,
        "source_commit": continuation.SOURCE_COMMIT,
        "protocol_commit": continuation.PROTOCOL_COMMIT,
        "operational_merge_commit": continuation.OPERATIONAL_MERGE_COMMIT,
        "prior_paid_approval_sha256": continuation.PRIOR_PAID_APPROVAL_SHA256,
        "assignment_sha256": MANIFEST["schedule"]["cell_assignment_sha256"],
        "original_journal_sha256": continuation.ORIGINAL_JOURNAL_SHA256,
        "stopped_continuation_journal_sha256": continuation.CONTINUATION_JOURNAL_SHA256,
        "stopped_continuation_log_sha256": continuation.CONTINUATION_LOG_SHA256,
        "stopped_cell4_paid_journal_sha256": continuation.STOP_JOURNAL_SHA256,
        "stopped_cell4_paid_log_sha256": continuation.STOP_LOG_SHA256,
        "pair7_preflight_sha256": continuation.PREFLIGHT7_SHA256,
        "retained_cell1_run_id": str(continuation.STOP_RUN_ID),
        "retained_cell4_run_id": str(continuation.CELL4_RUN_ID),
        "retained_provider_failed_run_ids": list(continuation.FAILED_RUNS),
        "first_paid_cell_order": 15,
        "last_paid_cell_order": 96,
        "paid_cells_authorized": 82,
        "retained_missing_orders": [1, 4],
        "retained_provider_failed_orders": [13, 14],
        "new_provider_health_epoch_start_order": 15,
        "stopped_estimated_cost_usd": str(continuation.STOP_ESTIMATED_COST),
        "stopped_conservative_cost_usd": str(continuation.STOP_CONSERVATIVE_COST),
        "no_retry_or_replacement": True,
        "cumulative_cost_ceiling_usd": str(continuation.CAP),
    }


def test_paid_continuation_is_pinned_and_exactly_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert continuation.EXPECTED_CELL14_APPROVAL_SHA256 == (
        "a9bfe3880e1f2d0eaa9773b588b9cde68138e5b4c19f7f3f643c4b78a2f3519a"
    )
    assert continuation.OPERATIONAL_MERGE_COMMIT == ("33af183147b60d05d3e0539428769266fec5946e")
    path = tmp_path / "cell14-paid-continuation-approval.json"
    monkeypatch.setattr(continuation, "EXPECTED_CELL14_APPROVAL_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="remains closed"):
        continuation._scope_approval(path, MANIFEST)
    receipt = _approval()

    def write() -> None:
        raw = json.dumps(receipt, sort_keys=True).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        monkeypatch.setattr(
            continuation, "EXPECTED_CELL14_APPROVAL_SHA256", hashlib.sha256(raw).hexdigest()
        )

    write()
    assert continuation._scope_approval(path, MANIFEST) == receipt
    for key, bad in (
        ("first_paid_cell_order", 14),
        ("paid_cells_authorized", 83),
        ("retained_provider_failed_orders", [13]),
        ("retained_provider_failed_run_ids", []),
        ("new_provider_health_epoch_start_order", 16),
        ("stopped_cell4_paid_journal_sha256", "0" * 64),
        ("prior_paid_approval_sha256", "0" * 64),
        ("operational_merge_commit", "0" * 40),
        ("no_retry_or_replacement", False),
    ):
        original = receipt[key]
        receipt[key] = bad
        write()
        with pytest.raises(ValueError, match="exact stopped scope"):
            continuation._scope_approval(path, MANIFEST)
        receipt[key] = original


def test_second_journal_starts_at_frozen_order_fifteen(tmp_path: Path) -> None:
    path = tmp_path / "cell14-paid-continuation-journal.jsonl"
    cell = MANIFEST["cells"][14]
    header = {
        "type": "batch_started",
        "manifest_sha256": continuation.EXPECTED_MANIFEST_SHA256,
        "expected_model_revision": continuation.REVISION,
        "expected_upstream_provider": continuation.UPSTREAM,
        "max_estimated_usd": float(continuation.CAP),
    }
    started = {"type": "cell_started", "cell_id": cell["cell_id"], "order": 15, "run_id": "run"}
    completed = {
        "type": "cell_completed",
        "cell_id": cell["cell_id"],
        "order": 15,
        "run_id": "run",
        "experiment_sha256": cell["experiment_sha256"],
    }
    path.write_text("".join(json.dumps(x) + "\n" for x in (header, started, completed)))
    assert len(postcheck._new_records(MANIFEST, path, start_order=15)) == 1
    started["order"] = 14
    path.write_text("".join(json.dumps(x) + "\n" for x in (header, started, completed)))
    with pytest.raises(ValueError, match="frozen order"):
        postcheck._new_records(MANIFEST, path, start_order=15)


def test_health_epoch_restarts_only_after_retained_pause() -> None:
    failed = {"status": "provider_failed", "failure_reason": "provider_unavailable"}
    completed = {"status": "budget_exhausted", "failure_reason": None}
    records = [completed] * 10 + [failed, failed, failed, failed]
    assert postcheck._health_stops_continuation(records, 11, second_journal=False)
    assert not postcheck._health_stops_continuation(records, 11, second_journal=True)
    assert not postcheck._health_stops_continuation(records, 12, second_journal=True)
    assert postcheck._health_stops_continuation(records, 13, second_journal=True)
