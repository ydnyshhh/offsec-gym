"""No-model gates for the one fresh M6.7.1 launch after the pre-cell failure."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m671_resume_after_precell_failure as recovery  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads(
    (ROOT / "experiments/manifests/m671-factorial-feasibility-v1.json").read_bytes()
)


def test_fresh_launch_is_separate_and_approval_is_pinned(tmp_path: Path) -> None:
    assert (
        recovery.NEW_APPROVAL_SHA256
        == "38af69f25668c3bcd53d50ce99f1dd189b9c153cfe81312ee612a1da9ee50fa0"
    )
    assert recovery.OPERATIONAL_MERGE_COMMIT == "eb3535709631aedab73990d1204a83c2c371cc7a"
    assert recovery.CONTINUATION_SCOPE != (
        "m671_feasibility_cells_87_through_128_once_after_provider_pause"
    )
    with pytest.raises(ValueError, match="private approval bytes, path, or mode differ"):
        recovery.scoped_approval(tmp_path / "precell-paid-approval.json", MANIFEST)


def test_fresh_launch_selects_only_unstarted_frozen_cells() -> None:
    pairs = recovery.pending_pairs(MANIFEST)
    assert [index for index, _ in pairs] == list(range(44, 65))
    assert [cell["order"] for _, pair in pairs for cell in pair] == list(range(87, 129))
    changed = json.loads(json.dumps(MANIFEST))
    changed["cells"][86]["order"] = 85
    with pytest.raises(ValueError, match="exact frozen paid range"):
        recovery.pending_pairs(changed)


def test_new_receipt_binds_consumed_launch_and_exact_paid_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge = "f" * 40
    monkeypatch.setattr(recovery, "OPERATIONAL_MERGE_COMMIT", merge)
    approval = {
        "approved_at": "2026-10-16T10:00:00Z",
        "approved_by": "human_user",
        "approved_for_paid_calls": True,
        "approval_scope": recovery.CONTINUATION_SCOPE,
        "protocol": recovery.PROTOCOL,
        "manifest_sha256": recovery.MANIFEST_SHA256,
        "assignment_sha256": MANIFEST["assignment_sha256"],
        "source_commit": recovery.SOURCE_COMMIT,
        "protocol_commit": recovery.PROTOCOL_COMMIT,
        "operational_merge_commit": merge,
        "prior_paid_approval_sha256": recovery.ORIGINAL_APPROVAL_SHA256,
        "consumed_continuation_approval_sha256": recovery.PREVIOUS_APPROVAL_SHA256,
        "order_fix_merge_commit": recovery.ORDER_FIX_MERGE_COMMIT,
        "failed_launch_journal_sha256": recovery.FAILED_JOURNAL_SHA256,
        "failed_launch_log_sha256": recovery.FAILED_LOG_SHA256,
        "failed_launch_clearance_sha256": recovery.FAILED_CLEARANCE_SHA256,
        "failed_launch_preflight_sha256": recovery.FAILED_PREFLIGHT_SHA256,
        "failed_launch_pair43_sha256": recovery.FAILED_PAIR43_SHA256,
        "failed_launch_started_cells": 0,
        "fresh_launch_count": 1,
        "stopped_journal_sha256": recovery.STOP_JOURNAL_SHA256,
        "stopped_log_sha256": recovery.STOP_LOG_SHA256,
        "stopped_pairs_sha256": recovery.STOP_PAIRS_SHA256,
        "stopped_preflights_sha256": recovery.STOP_PREFLIGHTS_SHA256,
        "stopped_completed_cells": recovery.STOP_COMPLETED,
        "stopped_source_runs": recovery.STOP_RUNS,
        "stopped_events": recovery.STOP_EVENTS,
        "stopped_pair_receipts": recovery.STOP_PAIRS,
        "retained_provider_failed_orders": [85, 86],
        "first_paid_cell_order": 87,
        "last_paid_cell_order": 128,
        "paid_cell_ids": [cell["cell_id"] for cell in MANIFEST["cells"][86:]],
        "paid_cells_authorized": 42,
        "provider_health_epoch_start_order": 87,
        "no_retry_or_replacement": True,
        "selected_endpoint": f"{recovery.UPSTREAM} | {recovery.REVISION}",
        "input_usd_per_million_tokens": str(recovery.INPUT_PRICE),
        "output_usd_per_million_tokens": str(recovery.OUTPUT_PRICE),
        "cumulative_estimated_cost_stop_usd": MANIFEST["cumulative_estimated_cost_stop_usd"],
    }
    path = tmp_path / "precell-paid-approval.json"
    raw = json.dumps(approval, sort_keys=True).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    monkeypatch.setattr(recovery, "NEW_APPROVAL_SHA256", recovery.sha(raw))
    assert recovery.scoped_approval(path, MANIFEST) == approval
    approval["failed_launch_started_cells"] = 1
    changed = json.dumps(approval, sort_keys=True).encode()
    path.write_bytes(changed)
    monkeypatch.setattr(recovery, "NEW_APPROVAL_SHA256", recovery.sha(changed))
    with pytest.raises(ValueError, match="exact stopped scope"):
        recovery.scoped_approval(path, MANIFEST)


def test_failed_launch_admission_requires_all_immutable_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = tmp_path / "provider-pause-continuation"
    pairs = failed / "pairs"
    old_pairs = tmp_path / "pairs"
    pairs.mkdir(parents=True)
    old_pairs.mkdir()
    for number in range(1, 43):
        name = f"pair-postcheck-{number}.json"
        (pairs / name).write_bytes(str(number).encode())
        (old_pairs / name).write_bytes(str(number).encode())
    expected = {
        "journal.jsonl": b'{"type":"batch_started"}\n',
        "clearance.json": b"clearance",
        "endpoint-preflight-43.json": b"preflight",
        "pairs/pair-postcheck-43.json": b"pair43",
    }
    for relative, content in expected.items():
        (failed / relative).write_bytes(content)
    log = tmp_path / "provider-pause-continuation.log"
    log.write_bytes(b"failed before cell start")
    for name, content in expected.items():
        field = {
            "journal.jsonl": "FAILED_JOURNAL_SHA256",
            "clearance.json": "FAILED_CLEARANCE_SHA256",
            "endpoint-preflight-43.json": "FAILED_PREFLIGHT_SHA256",
            "pairs/pair-postcheck-43.json": "FAILED_PAIR43_SHA256",
        }[name]
        monkeypatch.setattr(recovery, field, recovery.sha(content))
    monkeypatch.setattr(recovery, "FAILED_LOG_SHA256", recovery.sha(log.read_bytes()))
    monkeypatch.setattr(
        recovery,
        "STOP_PAIRS_SHA256",
        recovery.file_set_digest(sorted(old_pairs.glob("pair-postcheck-*.json"))),
    )
    recovery.admit_failed_launch(tmp_path)
    (failed / "journal.jsonl").write_bytes(
        b'{"type":"batch_started"}\n{"type":"cell_started","order":87}\n'
    )
    with pytest.raises(ValueError, match="failed-launch evidence bytes differ"):
        recovery.admit_failed_launch(tmp_path)


def test_fresh_journal_header_cannot_bind_consumed_scope() -> None:
    stopped = b'{"type":"batch_started"}\n'
    reused = (
        json.dumps(
            {
                "type": "batch_started",
                "protocol": recovery.PROTOCOL,
                "manifest_sha256": recovery.MANIFEST_SHA256,
                "continuation_scope": (
                    "m671_feasibility_cells_87_through_128_once_after_provider_pause"
                ),
                "stopped_journal_sha256": recovery.STOP_JOURNAL_SHA256,
                "approval_sha256": recovery.PREVIOUS_APPROVAL_SHA256,
                "operational_merge_commit": recovery.ORDER_FIX_MERGE_COMMIT,
            }
        )
        + "\n"
    ).encode()
    with pytest.raises(ValueError, match="header differs"):
        recovery.combined_journal(stopped, reused)
