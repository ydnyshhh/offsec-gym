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


def test_fresh_launch_is_separate_and_closed_until_pinned(tmp_path: Path) -> None:
    assert recovery.NEW_APPROVAL_SHA256 == "0" * 64
    assert recovery.OPERATIONAL_MERGE_COMMIT == "0" * 40
    assert recovery.CONTINUATION_SCOPE != (
        "m671_feasibility_cells_87_through_128_once_after_provider_pause"
    )
    with pytest.raises(ValueError, match="closed until exact approval pin"):
        recovery.scoped_approval(tmp_path / "precell-paid-approval.json", MANIFEST)


def test_fresh_launch_selects_only_unstarted_frozen_cells() -> None:
    pairs = recovery.pending_pairs(MANIFEST)
    assert [index for index, _ in pairs] == list(range(44, 65))
    assert [cell["order"] for _, pair in pairs for cell in pair] == list(range(87, 129))
    changed = json.loads(json.dumps(MANIFEST))
    changed["cells"][86]["order"] = 85
    with pytest.raises(ValueError, match="exact frozen paid range"):
        recovery.pending_pairs(changed)


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
