"""Exact-stop guards for the proposed M6.6.1 provider-pause recovery."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m661_cell14_provider_stop as stopped  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads((ROOT / "experiments/manifests/m661-feasibility-v1.json").read_bytes())


def _journal(path: Path) -> list[dict]:
    records = []
    lines = [
        {
            "type": "batch_started",
            "manifest_sha256": stopped.EXPECTED_MANIFEST_SHA256,
            "expected_model_revision": stopped.REVISION,
            "expected_upstream_provider": stopped.UPSTREAM,
            "max_estimated_usd": float(stopped.CAP),
        }
    ]
    for order in range(5, 15):
        cell = MANIFEST["cells"][order - 1]
        offset = order - 5
        record = {
            "type": "cell_completed",
            "order": order,
            "cell_id": cell["cell_id"],
            "run_id": stopped.FAILED_RUNS[offset - 8] if order >= 13 else f"run-{order}",
            "experiment_sha256": cell["experiment_sha256"],
            "status": "provider_failed" if order >= 13 else "budget_exhausted",
            "failure_reason": "provider_unavailable" if order >= 13 else None,
            "score_valid": order < 13,
            "resource_auditable": order < 13,
            "estimated_cost_usd": (
                "3.326619" if order == 12 else "0.155850" if order == 13 else "0"
            ),
            "trace_sha256": stopped.FAILED_TRACE_SHA256[offset - 8] if order >= 13 else "0" * 64,
            "event_identity_sha256": stopped.FAILED_IDENTITY_SHA256[offset - 8]
            if order >= 13
            else "0" * 64,
        }
        records.append(record)
        lines.extend(
            (
                {
                    "type": "cell_started",
                    "order": order,
                    "cell_id": cell["cell_id"],
                    "run_id": record["run_id"],
                },
                record,
            )
        )
    path.write_text("".join(json.dumps(item) + "\n" for item in lines))
    return records


def test_pinned_stop_accepts_only_frozen_failure_and_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cell4-paid-continuation-journal.jsonl"
    records = _journal(path)
    monkeypatch.setattr(
        stopped, "STOP_JOURNAL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert stopped._pinned_records(MANIFEST, tmp_path) == records

    records[-1]["score_valid"] = True
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    lines[-1] = records[-1]
    path.write_text("".join(json.dumps(item) + "\n" for item in lines))
    monkeypatch.setattr(
        stopped, "STOP_JOURNAL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="retained provider failure"):
        stopped._pinned_records(MANIFEST, tmp_path)

    records[-1]["score_valid"] = False
    records[-1]["estimated_cost_usd"] = "0.1"
    lines[-1] = records[-1]
    path.write_text("".join(json.dumps(item) + "\n" for item in lines))
    monkeypatch.setattr(
        stopped, "STOP_JOURNAL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="stopped cost"):
        stopped._pinned_records(MANIFEST, tmp_path)


@pytest.mark.asyncio
async def test_admission_accepts_only_expected_missing_pair_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    files = {
        "journal.jsonl": "original",
        "continuation-journal.jsonl": "old continuation",
        "collector-cell4-paid-session.log": "stop log",
        "pair-postcheck-6.json": "pair six",
        "endpoint-preflight-7.json": json.dumps(
            {
                "endpoint": f"{stopped.UPSTREAM} | {stopped.REVISION}",
                "input_usd_per_million_tokens": 3.0,
                "output_usd_per_million_tokens": 15.0,
            }
        ),
    }
    for name, value in files.items():
        (private / name).write_text(value)
    for attr, name in (
        ("ORIGINAL_JOURNAL_SHA256", "journal.jsonl"),
        ("CONTINUATION_JOURNAL_SHA256", "continuation-journal.jsonl"),
        ("STOP_LOG_SHA256", "collector-cell4-paid-session.log"),
        ("PAIR6_SHA256", "pair-postcheck-6.json"),
        ("PREFLIGHT7_SHA256", "endpoint-preflight-7.json"),
    ):
        monkeypatch.setattr(
            stopped, attr, hashlib.sha256((private / name).read_bytes()).hexdigest()
        )
    monkeypatch.setattr(stopped, "_approval_and_manifest", lambda *_: (MANIFEST, {}))
    monkeypatch.setattr(stopped, "_pinned_records", lambda *_: [None] * 10)
    monkeypatch.setattr(stopped, "_no_collector_running", lambda: None)

    async def missing_pair(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FileNotFoundError(2, "absent", private / "pair-postcheck-7.json")

    monkeypatch.setattr(stopped, "postcheck", missing_pair)
    args = (tmp_path, tmp_path, tmp_path, tmp_path / "manifest.json", private / "approval.json")
    result = await stopped.admit(*args, private / "journal.jsonl", tmp_path / "state")
    assert result["next_unstarted_order"] == 15

    async def wrong_missing(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FileNotFoundError(2, "absent", private / "pair-postcheck-6.json")

    monkeypatch.setattr(stopped, "postcheck", wrong_missing)
    with pytest.raises(FileNotFoundError):
        await stopped.admit(*args, private / "journal.jsonl", tmp_path / "state")
    (private / "pair-postcheck-7.json").write_text("unexpected")
    with pytest.raises(ValueError, match="stop evidence differs"):
        await stopped.admit(*args, private / "journal.jsonl", tmp_path / "state")
