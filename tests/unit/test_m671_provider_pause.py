"""No-model integrity checks for the stopped M6.7.1 continuation boundary."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from offsecgym.research.m671_postcheck import journal_prefix

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m671_provider_pause_stop as stopped  # noqa: E402
import m671_resume_after_provider_pause as recovery  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads(
    (ROOT / "experiments/manifests/m671-factorial-feasibility-v1.json").read_bytes()
)


def _started(cell: dict[str, object]) -> dict[str, object]:
    return {
        "type": "cell_started",
        "cell_id": cell["cell_id"],
        "order": cell["order"],
        "run_id": f"00000000-0000-0000-0000-{int(cell['order']):012d}",
    }


def _completed(cell: dict[str, object]) -> dict[str, object]:
    return {
        **_started(cell),
        "type": "cell_completed",
        "experiment_sha256": cell["experiment_sha256"],
        "terminal_status": "provider_failed" if cell["order"] in (85, 86) else "budget_exhausted",
        "failure_reason": "provider_unavailable" if cell["order"] in (85, 86) else None,
        "estimated_cost_usd": None if cell["order"] in (85, 86) else "0.1",
    }


def _journal(cells: list[dict[str, object]]) -> bytes:
    lines = [
        {
            "type": "batch_started",
            "protocol": recovery.PROTOCOL,
            "manifest_sha256": stopped.MANIFEST_SHA256,
        }
    ]
    for cell in cells:
        lines += [_started(cell), _completed(cell)]
    return ("\n".join(json.dumps(row, sort_keys=True) for row in lines) + "\n").encode()


def _continuation_journal(cells: list[dict[str, object]]) -> bytes:
    lines = _journal(cells).splitlines()
    lines[0] = json.dumps(
        {
            "type": "batch_started",
            "protocol": recovery.PROTOCOL,
            "manifest_sha256": stopped.MANIFEST_SHA256,
            "continuation_scope": recovery.CONTINUATION_SCOPE,
            "stopped_journal_sha256": recovery.STOP_JOURNAL_SHA256,
            "approval_sha256": recovery.NEW_APPROVAL_SHA256,
            "operational_merge_commit": recovery.OPERATIONAL_MERGE_COMMIT,
        },
        sort_keys=True,
    ).encode()
    return b"\n".join(lines) + b"\n"


def test_approval_pin_is_closed_and_original_failures_do_not_enter_new_health_epoch(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="closed until exact approval pin"):
        recovery.scoped_approval(tmp_path / "provider-pause-paid-approval.json", MANIFEST)
    assert recovery.health_decision([]) == "continue"
    assert (
        recovery.health_decision(
            [{"terminal_status": "provider_failed", "failure_reason": "provider_unavailable"}] * 2
        )
        == "pause"
    )


def test_exact_private_scope_accepts_only_frozen_unstarted_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "provider-pause-paid-approval.json"
    merge = "f" * 40
    monkeypatch.setattr(recovery, "OPERATIONAL_MERGE_COMMIT", merge)
    approval = {
        "approved_at": "2026-10-16T06:00:00Z",
        "approved_by": "human_user",
        "approved_for_paid_calls": True,
        "approval_scope": recovery.CONTINUATION_SCOPE,
        "protocol": recovery.PROTOCOL,
        "manifest_sha256": stopped.MANIFEST_SHA256,
        "assignment_sha256": MANIFEST["assignment_sha256"],
        "source_commit": recovery.SOURCE_COMMIT,
        "protocol_commit": recovery.PROTOCOL_COMMIT,
        "operational_merge_commit": merge,
        "prior_paid_approval_sha256": recovery.ORIGINAL_APPROVAL_SHA256,
        "stopped_journal_sha256": stopped.STOP_JOURNAL_SHA256,
        "stopped_log_sha256": stopped.STOP_LOG_SHA256,
        "stopped_pairs_sha256": stopped.STOP_PAIRS_SHA256,
        "stopped_preflights_sha256": stopped.STOP_PREFLIGHTS_SHA256,
        "stopped_completed_cells": 86,
        "stopped_source_runs": 172,
        "stopped_events": 143873,
        "stopped_pair_receipts": 42,
        "retained_provider_failed_orders": [85, 86],
        "first_paid_cell_order": 87,
        "last_paid_cell_order": 128,
        "paid_cell_ids": [cell["cell_id"] for cell in MANIFEST["cells"][86:]],
        "paid_cells_authorized": 42,
        "provider_health_epoch_start_order": 87,
        "no_retry_or_replacement": True,
        "selected_endpoint": f"{recovery.UPSTREAM} | {recovery.REVISION}",
        "input_usd_per_million_tokens": "3",
        "output_usd_per_million_tokens": "15",
        "cumulative_estimated_cost_stop_usd": MANIFEST["cumulative_estimated_cost_stop_usd"],
    }
    raw = json.dumps(approval, sort_keys=True).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    monkeypatch.setattr(recovery, "NEW_APPROVAL_SHA256", stopped.sha(raw))
    assert recovery.scoped_approval(path, MANIFEST) == approval
    approval["paid_cell_ids"] = approval["paid_cell_ids"][:-1]
    changed = json.dumps(approval, sort_keys=True).encode()
    path.write_bytes(changed)
    monkeypatch.setattr(recovery, "NEW_APPROVAL_SHA256", stopped.sha(changed))
    with pytest.raises(ValueError, match="exact stopped scope"):
        recovery.scoped_approval(path, MANIFEST)


def test_combined_journal_preserves_exact_order_and_rejects_replay() -> None:
    old = _journal(MANIFEST["cells"][:86])
    later = _continuation_journal(MANIFEST["cells"][86:])
    combined = recovery.combined_journal(old, later)
    records = journal_prefix(MANIFEST, combined, manifest_sha256=stopped.MANIFEST_SHA256)
    assert len(records) == 128
    assert [row["order"] for row in records] == list(range(1, 129))
    replayed = _continuation_journal([MANIFEST["cells"][85], *MANIFEST["cells"][87:]])
    with pytest.raises(ValueError, match="unique frozen-order prefix"):
        journal_prefix(
            MANIFEST,
            recovery.combined_journal(old, replayed),
            manifest_sha256=stopped.MANIFEST_SHA256,
        )
    wrong_header = later.replace(recovery.CONTINUATION_SCOPE.encode(), b"wrong_scope", 1)
    with pytest.raises(ValueError, match="header differs"):
        recovery.combined_journal(old, wrong_header)


def test_continuation_journal_is_create_once_and_private(tmp_path: Path) -> None:
    path = tmp_path / "continuation.jsonl"
    journal = recovery.ContinuationJournal(path)
    journal.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_bytes().splitlines()[0])["continuation_scope"] == (
        recovery.CONTINUATION_SCOPE
    )
    with pytest.raises(FileExistsError):
        recovery.ContinuationJournal(path)


def test_stopped_evidence_is_exact_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path
    pairs = private / "pairs"
    pairs.mkdir()
    journal = _journal(MANIFEST["cells"][:86])
    (private / "journal.jsonl").write_bytes(journal)
    (private / "collector.log").write_bytes(b"stopped provider gate\n")
    for index in range(1, 44):
        (private / f"endpoint-preflight-{index}.json").write_bytes(f"endpoint {index}\n".encode())
    for index in range(1, 43):
        cells = MANIFEST["cells"][2 * index - 2 : 2 * index]
        preflight = private / f"endpoint-preflight-{index}.json"
        receipt = {
            "manifest_sha256": stopped.MANIFEST_SHA256,
            "pair_id": cells[0]["pair_id"],
            "cell_ids": [cell["cell_id"] for cell in cells],
            "run_ids": [_started(cell)["run_id"] for cell in cells],
            "endpoint_preflight_sha256": stopped.sha(preflight.read_bytes()),
        }
        (pairs / f"pair-postcheck-{index}.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(stopped, "STOP_JOURNAL_SHA256", stopped.sha(journal))
    monkeypatch.setattr(
        stopped, "STOP_LOG_SHA256", stopped.sha((private / "collector.log").read_bytes())
    )
    monkeypatch.setattr(
        stopped,
        "STOP_PAIRS_SHA256",
        stopped.file_set_digest(sorted(pairs.glob("pair-postcheck-*.json"))),
    )
    monkeypatch.setattr(
        stopped,
        "STOP_PREFLIGHTS_SHA256",
        stopped.file_set_digest(sorted(private.glob("endpoint-preflight-*.json"))),
    )
    assert len(stopped.stopped_records(MANIFEST, private)) == 86
    with (private / "endpoint-preflight-43.json").open("ab") as stream:
        stream.write(b"drift")
    with pytest.raises(ValueError, match="stopped pair or endpoint evidence changed"):
        stopped.stopped_records(MANIFEST, private)
