"""Offline safety checks for the stopped cell-four permission failure."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m661_cached_build_permissions as permissions  # noqa: E402
import m661_cell4_stop as stop  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads((ROOT / "experiments/manifests/m661-feasibility-v1.json").read_bytes())


def test_new_paid_scope_is_closed_before_receipt_read(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="closed pending exact human approval"):
        permissions._new_approval(tmp_path / "cell4-continuation-approval.json", MANIFEST)


def test_new_scope_binds_stopped_log_and_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cell4-continuation-approval.json"
    receipt = {
        "approved_at": "2026-10-12T10:00:00Z",
        "approved_by": "human_user",
        "approved_for_paid_calls": True,
        "approval_scope": permissions.SCOPE,
        "manifest_sha256": permissions.EXPECTED_MANIFEST_SHA256,
        "source_commit": permissions.SOURCE_COMMIT,
        "protocol_commit": permissions.PROTOCOL_COMMIT,
        "assignment_sha256": MANIFEST["schedule"]["cell_assignment_sha256"],
        "stopped_continuation_journal_sha256": stop.CONTINUATION_JOURNAL_SHA256,
        "stopped_continuation_log_sha256": stop.CONTINUATION_LOG_SHA256,
        "retained_cell4_run_id": str(stop.CELL4_RUN_ID),
        "cached_build_id": str(stop.STOP_BUILD_ID),
        "first_paid_cell_order": 5,
        "last_paid_cell_order": 96,
        "no_retry_or_replacement": True,
        "mode_only_cached_build_repair": True,
        "cumulative_cost_ceiling_usd": str(permissions.CAP),
    }

    def write_receipt() -> None:
        raw = json.dumps(receipt, sort_keys=True).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        monkeypatch.setattr(
            permissions, "EXPECTED_NEW_APPROVAL_SHA256", hashlib.sha256(raw).hexdigest()
        )

    write_receipt()
    assert permissions._new_approval(path, MANIFEST) == receipt
    receipt["stopped_continuation_log_sha256"] = "0" * 64
    write_receipt()
    with pytest.raises(ValueError, match="exact stopped scope"):
        permissions._new_approval(path, MANIFEST)


def test_repair_gate_rejects_concurrent_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    other = os.getpid() + 1000
    monkeypatch.setattr(
        permissions.subprocess,
        "check_output",
        lambda *_args, **_kwargs: f"{other} python research_ops/m661_feasibility_collect.py\n",
    )
    with pytest.raises(ValueError, match="another M6.6.1 collector"):
        permissions._no_collector_running()


def test_mode_only_repair_on_temporary_copy_preserves_bytes(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    original = {"init_db.py": b"print('ready')\n", "service.py": b"VALUE=1\n"}
    for name, raw in original.items():
        path = build / name
        path.write_bytes(raw)
        path.chmod(0o600)
    plan = permissions._mode_plan(build, set(original))
    assert set(plan) == set(original)
    permissions._apply_mode_plan(build, plan)
    assert {
        name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for name, path in ((name, build / name) for name in original)
    } == {name: (raw, 0o644) for name, raw in original.items()}
    assert {name: plan[name]["sha256"] for name in original} == {
        name: hashlib.sha256(raw).hexdigest() for name, raw in original.items()
    }


def test_mode_repair_rejects_changed_or_symlinked_file(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    path = build / "init_db.py"
    path.write_bytes(b"one")
    path.chmod(0o600)
    plan = permissions._mode_plan(build, {path.name})
    path.write_bytes(b"two")
    with pytest.raises(ValueError, match="changed after permission plan"):
        permissions._apply_mode_plan(build, plan)
    path.unlink()
    path.symlink_to(tmp_path / "other")
    with pytest.raises(ValueError, match="unsafe"):
        permissions._mode_plan(build, {path.name})


def test_stop_journal_requires_exact_frozen_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path
    cells = MANIFEST["cells"]
    rows = [
        {
            "type": "batch_started",
            "manifest_sha256": stop.EXPECTED_MANIFEST_SHA256,
            "expected_model_revision": stop.REVISION,
            "expected_upstream_provider": stop.UPSTREAM,
            "max_estimated_usd": float(stop.CAP),
        }
    ]
    for order in (2, 3):
        run_id = f"00000000-0000-0000-0000-{order:012d}"
        cell = cells[order - 1]
        rows.append(
            {"type": "cell_started", "cell_id": cell["cell_id"], "order": order, "run_id": run_id}
        )
        rows.append(
            {
                "type": "cell_completed",
                "cell_id": cell["cell_id"],
                "order": order,
                "run_id": run_id,
                "experiment_sha256": cell["experiment_sha256"],
            }
        )
    rows.append(
        {
            "type": "cell_started",
            "cell_id": cells[3]["cell_id"],
            "order": 4,
            "run_id": str(stop.CELL4_RUN_ID),
        }
    )
    raw = b"".join((json.dumps(row) + "\n").encode() for row in rows)
    (private / "continuation-journal.jsonl").write_bytes(raw)
    (private / "collector-cell1-continuation.log").write_bytes(b"stopped")
    monkeypatch.setattr(stop, "CONTINUATION_JOURNAL_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(stop, "CONTINUATION_LOG_SHA256", hashlib.sha256(b"stopped").hexdigest())
    records, started = stop._stopped_records(MANIFEST, private)
    assert len(records) == 2 and started["order"] == 4
    rows[-1]["order"] = 5
    changed = b"".join((json.dumps(row) + "\n").encode() for row in rows)
    (private / "continuation-journal.jsonl").write_bytes(changed)
    monkeypatch.setattr(stop, "CONTINUATION_JOURNAL_SHA256", hashlib.sha256(changed).hexdigest())
    with pytest.raises(ValueError, match="cell four start"):
        stop._stopped_records(MANIFEST, private)
