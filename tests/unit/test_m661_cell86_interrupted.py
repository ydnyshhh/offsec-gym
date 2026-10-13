"""Exact interrupted-cell guards for the proposed M6.6.1 continuation."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m661_cell86_interrupted_stop as stopped  # noqa: E402
import m661_cell86_recovery_postcheck as postcheck  # noqa: E402
import m661_resume_after_cell86_permission as continuation  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads((ROOT / "experiments/manifests/m661-feasibility-v1.json").read_bytes())


def test_interrupted_prefix_retains_exactly_one_unmatched_cell_86(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cell14-paid-continuation-journal.jsonl"
    lines = [
        {
            "type": "batch_started",
            "manifest_sha256": stopped.EXPECTED_MANIFEST_SHA256,
            "expected_model_revision": stopped.REVISION,
            "expected_upstream_provider": stopped.UPSTREAM,
            "max_estimated_usd": float(stopped.CAP),
        }
    ]
    for order in range(15, 86):
        cell = MANIFEST["cells"][order - 1]
        started = {
            "type": "cell_started",
            "cell_id": cell["cell_id"],
            "order": order,
            "run_id": f"run-{order}",
        }
        completed = {
            **started,
            "type": "cell_completed",
            "experiment_sha256": cell["experiment_sha256"],
        }
        lines.extend((started, completed))
    cell86 = MANIFEST["cells"][85]
    lines.append(
        {
            "type": "cell_started",
            "cell_id": cell86["cell_id"],
            "order": 86,
            "run_id": str(stopped.INTERRUPTED_RUN_ID),
        }
    )

    def write() -> None:
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        monkeypatch.setattr(
            stopped, "STOP_JOURNAL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
        )

    write()
    records, tail = stopped._completed_prefix(MANIFEST, tmp_path)
    assert [record["order"] for record in records] == list(range(15, 86))
    assert tail["order"] == 86
    lines[-1]["order"] = 87
    write()
    with pytest.raises(ValueError, match="interrupted assignment"):
        stopped._completed_prefix(MANIFEST, tmp_path)
    lines[-1]["order"] = 86
    lines[-1]["type"] = "cell_completed"
    write()
    with pytest.raises(ValueError, match="unmatched start"):
        stopped._completed_prefix(MANIFEST, tmp_path)


def test_later_journal_rejects_retry_and_incomplete_cell(tmp_path: Path) -> None:
    path = tmp_path / "cell86-paid-continuation-journal.jsonl"
    cell87 = MANIFEST["cells"][86]
    header = {
        "type": "batch_started",
        "manifest_sha256": stopped.EXPECTED_MANIFEST_SHA256,
        "expected_model_revision": stopped.REVISION,
        "expected_upstream_provider": stopped.UPSTREAM,
        "max_estimated_usd": float(stopped.CAP),
    }
    started = {
        "type": "cell_started",
        "cell_id": cell87["cell_id"],
        "order": 87,
        "run_id": "run-87",
    }
    completed = {
        **started,
        "type": "cell_completed",
        "experiment_sha256": cell87["experiment_sha256"],
    }
    path.write_text("".join(json.dumps(line) + "\n" for line in (header, started, completed)))
    assert len(postcheck._third_records(MANIFEST, path)) == 1
    started["order"] = 86
    path.write_text("".join(json.dumps(line) + "\n" for line in (header, started, completed)))
    with pytest.raises(ValueError, match="frozen order"):
        postcheck._third_records(MANIFEST, path)
    started["order"] = 87
    path.write_text("".join(json.dumps(line) + "\n" for line in (header, started)))
    with pytest.raises(ValueError, match="paired records"):
        postcheck._third_records(MANIFEST, path)


def test_paid_scope_is_closed_and_cannot_include_cell_86(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert continuation.EXPECTED_CELL86_APPROVAL_SHA256 == "0" * 64
    assert continuation.OPERATIONAL_MERGE_COMMIT == "0" * 40
    path = tmp_path / "cell86-paid-continuation-approval.json"
    with pytest.raises(ValueError, match="closed pending exact human approval"):
        continuation._scope_approval(path, MANIFEST)
    receipt = {
        "approved_at": "2026-10-14T00:00:00Z",
        "approved_by": "human_user",
        "approved_for_paid_calls": True,
        "approval_scope": continuation.CONTINUATION_SCOPE,
        "protocol": MANIFEST["protocol"],
        "manifest_sha256": continuation.EXPECTED_MANIFEST_SHA256,
        "source_commit": continuation.SOURCE_COMMIT,
        "protocol_commit": continuation.PROTOCOL_COMMIT,
        "operational_merge_commit": "1" * 40,
        "prior_paid_approval_sha256": continuation.EXPECTED_CELL14_APPROVAL_SHA256,
        "assignment_sha256": MANIFEST["schedule"]["cell_assignment_sha256"],
        "stopped_journal_sha256": continuation.STOP_JOURNAL_SHA256,
        "stopped_log_sha256": continuation.STOP_LOG_SHA256,
        "interrupted_run_id": str(continuation.INTERRUPTED_RUN_ID),
        "interrupted_trace_sha256": continuation.INTERRUPTED_TRACE_SHA256,
        "interrupted_event_identity_sha256": continuation.INTERRUPTED_IDENTITY_SHA256,
        "unsettled_call_id": str(continuation.UNSETTLED_CALL_ID),
        "unsettled_reservation_microusd": continuation.UNSETTLED_RESERVED_COST_MICROUSD,
        "interrupted_cell_conservative_usd": str(continuation.CELL_RESERVATION),
        "first_paid_cell_order": 87,
        "last_paid_cell_order": 96,
        "paid_cells_authorized": 10,
        "retained_missing_orders": [1, 4, 86],
        "retained_provider_failed_orders": [13, 14],
        "provider_health_epoch_start_order": 15,
        "no_retry_or_replacement": True,
        "cumulative_cost_ceiling_usd": str(continuation.CAP),
    }

    def write() -> None:
        raw = json.dumps(receipt, sort_keys=True).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        monkeypatch.setattr(
            continuation, "EXPECTED_CELL86_APPROVAL_SHA256", hashlib.sha256(raw).hexdigest()
        )

    monkeypatch.setattr(continuation, "OPERATIONAL_MERGE_COMMIT", "1" * 40)
    write()
    assert continuation._scope_approval(path, MANIFEST) == receipt
    for key, bad in (
        ("first_paid_cell_order", 86),
        ("paid_cells_authorized", 11),
        ("retained_missing_orders", [1, 4]),
        ("unsettled_call_id", "different"),
        ("no_retry_or_replacement", False),
    ):
        original = receipt[key]
        receipt[key] = bad
        write()
        with pytest.raises(ValueError, match="exact stopped scope"):
            continuation._scope_approval(path, MANIFEST)
        receipt[key] = original
