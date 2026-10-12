"""The M6.6.1 cell-1 infrastructure incident is retained, never retried."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m661_cell1_recovery as recovery  # noqa: E402
import m661_cell1_recovery_postcheck as postcheck  # noqa: E402
import m661_resume_after_cell1_permission as resume  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads((ROOT / "experiments/manifests/m661-feasibility-v1.json").read_bytes())


def test_stopped_prefix_rejects_changed_or_second_assignment(tmp_path: Path, monkeypatch) -> None:
    first = MANIFEST["cells"][0]
    header = {
        "type": "batch_started",
        "manifest_sha256": recovery.EXPECTED_MANIFEST_SHA256,
        "expected_model_revision": recovery.REVISION,
        "expected_upstream_provider": recovery.UPSTREAM,
        "max_estimated_usd": 172.8,
    }
    started = {
        "type": "cell_started",
        "cell_id": first["cell_id"],
        "order": 1,
        "run_id": str(recovery.STOP_RUN_ID),
    }
    path = tmp_path / "journal.jsonl"
    raw = (json.dumps(header) + "\n" + json.dumps(started) + "\n").encode()
    path.write_bytes(raw)
    monkeypatch.setattr(recovery, "STOP_JOURNAL_SHA256", recovery._sha256(raw))
    assert recovery._stopped_journal(MANIFEST, path) == first
    path.write_bytes(raw + raw)
    with pytest.raises(ValueError, match="bytes differ"):
        recovery._stopped_journal(MANIFEST, path)
    changed = {**started, "cell_id": MANIFEST["cells"][1]["cell_id"]}
    raw = (json.dumps(header) + "\n" + json.dumps(changed) + "\n").encode()
    path.write_bytes(raw)
    monkeypatch.setattr(recovery, "STOP_JOURNAL_SHA256", recovery._sha256(raw))
    with pytest.raises(ValueError, match="first assignment differs"):
        recovery._stopped_journal(MANIFEST, path)


def test_continuation_is_closed_without_separate_approval(tmp_path: Path) -> None:
    assert resume.EXPECTED_CONTINUATION_APPROVAL_SHA256 == "0" * 64
    with pytest.raises(ValueError, match="separate human approval"):
        resume._scope_approval(tmp_path / "cell1-continuation-approval.json", MANIFEST)


@pytest.mark.asyncio
async def test_closed_scope_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(resume, "_approval_and_manifest", lambda *_args: (MANIFEST, {}))
    with pytest.raises(ValueError, match="separate human approval"):
        await resume.continue_cells(
            tmp_path,
            tmp_path,
            tmp_path,
            tmp_path / "manifest.json",
            tmp_path / "approval.json",
            tmp_path / "cell1-continuation-approval.json",
            tmp_path / "journal.jsonl",
            tmp_path / "range-state",
        )
    assert list(tmp_path.iterdir()) == []


def test_continuation_journal_starts_at_cell_two_without_repeating_one(tmp_path: Path) -> None:
    second = MANIFEST["cells"][1]
    header = {
        "type": "batch_started",
        "manifest_sha256": recovery.EXPECTED_MANIFEST_SHA256,
        "expected_model_revision": recovery.REVISION,
        "expected_upstream_provider": recovery.UPSTREAM,
        "max_estimated_usd": 172.8,
    }
    started = {
        "type": "cell_started",
        "cell_id": second["cell_id"],
        "order": 2,
        "run_id": "00000000-0000-0000-0000-000000000002",
    }
    completed = {
        **started,
        "type": "cell_completed",
        "experiment_sha256": second["experiment_sha256"],
    }
    path = tmp_path / "continuation-journal.jsonl"
    path.write_text("".join(json.dumps(item) + "\n" for item in (header, started, completed)))
    assert postcheck._continuation_records(MANIFEST, path) == [completed]
    repeated = {**started, "cell_id": MANIFEST["cells"][0]["cell_id"], "order": 1}
    path.write_text("".join(json.dumps(item) + "\n" for item in (header, repeated, completed)))
    with pytest.raises(ValueError, match="assigned order"):
        postcheck._continuation_records(MANIFEST, path)
    path.write_text("".join(json.dumps(item) + "\n" for item in (header, started)))
    with pytest.raises(ValueError, match="interrupted continuation"):
        postcheck._continuation_records(MANIFEST, path)


def test_infrastructure_missing_row_keeps_outcome_missing_with_known_zero_usage() -> None:
    from offsecgym.research import m661_checkpoint_analysis as analysis

    row = postcheck._first_missing_row(
        analysis, MANIFEST["cells"][0], {"run_id": str(recovery.STOP_RUN_ID)}
    )
    assert row["assigned"] is True
    assert row["terminal_status"] == "environment_failed"
    assert row["trace_auditable"] is False
    assert row["full_transition"] is None
    assert row["resource_auditable"] is True
    assert row["model_calls"] == row["input_tokens"] == row["output_tokens"] == 0


@pytest.mark.asyncio
async def test_approved_path_starts_only_cell_two_before_any_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BeforeModel(Exception):
        pass

    class NoModelRunner:
        def __init__(self, *_args: object) -> None:
            pass

        async def run(self, _spec: object, *, run_id: object) -> None:
            raise BeforeModel(str(run_id))

    private_dir = tmp_path / "private"
    private_dir.mkdir()
    journal = private_dir / "journal.jsonl"
    journal.write_bytes(b"frozen-original-journal\n")
    monkeypatch.setattr(resume, "STOP_JOURNAL_SHA256", recovery._sha256(journal.read_bytes()))
    monkeypatch.setattr(resume, "_approval_and_manifest", lambda *_args: (MANIFEST, {}))
    monkeypatch.setattr(resume, "_scope_approval", lambda *_args: {})
    monkeypatch.setattr(resume, "_no_collector_running", lambda: None)
    monkeypatch.setattr(resume, "_spec", lambda *_args: object())
    monkeypatch.setattr(resume, "_endpoint", lambda *_args: {"endpoint": "qualified-no-model"})
    monkeypatch.setattr(resume, "_load_module", lambda *_args: object())
    monkeypatch.setattr(resume, "CriticalCheckpointExperimentRunner", NoModelRunner)

    async def exact_first(*_args: object) -> dict:
        return {"run_id": str(recovery.STOP_RUN_ID), "endpoint_preflight_sha256": "test"}

    monkeypatch.setattr(resume, "reconcile", exact_first)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-never-used")
    monkeypatch.setenv("OFFSECGYM_DATABASE_URL", "postgresql+asyncpg://unused/unused")
    old_mask = os.umask(0o077)
    try:
        with pytest.raises(BeforeModel):
            await resume.continue_cells(
                tmp_path,
                tmp_path,
                tmp_path,
                tmp_path / "manifest.json",
                private_dir / "approval.json",
                private_dir / "cell1-continuation-approval.json",
                journal,
                private_dir / "range-state",
            )
    finally:
        os.umask(old_mask)
    assert journal.read_bytes() == b"frozen-original-journal\n"
    lines = [
        json.loads(line)
        for line in (private_dir / "continuation-journal.jsonl").read_text().splitlines()
    ]
    assert [line["type"] for line in lines] == ["batch_started", "cell_started"]
    assert lines[1]["order"] == 2
    assert lines[1]["cell_id"] == MANIFEST["cells"][1]["cell_id"]
    assert len(list(private_dir.glob("pair-postcheck-*.json"))) == 0
