"""No-model integrity checks for the stopped M6.7.1 continuation boundary."""

from __future__ import annotations

import json
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


def test_combined_journal_preserves_exact_order_and_rejects_replay() -> None:
    old = _journal(MANIFEST["cells"][:86])
    later = _journal(MANIFEST["cells"][86:])
    combined = recovery.combined_journal(old, later)
    records = journal_prefix(MANIFEST, combined, manifest_sha256=stopped.MANIFEST_SHA256)
    assert len(records) == 128
    assert [row["order"] for row in records] == list(range(1, 129))
    replayed = _journal([MANIFEST["cells"][85], *MANIFEST["cells"][87:]])
    with pytest.raises(ValueError, match="unique frozen-order prefix"):
        journal_prefix(
            MANIFEST,
            recovery.combined_journal(old, replayed),
            manifest_sha256=stopped.MANIFEST_SHA256,
        )


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
