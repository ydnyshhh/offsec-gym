"""Versioned, fail-closed mode-only repair for the cell-1 cached build.

The frozen compiler output bytes remain unchanged. This helper has no model or
endpoint client and grants no paid-cell authority. Its real-study entry point
is closed until a repair-only human receipt is pinned in a separate commit.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from m661_cell4_stop import (
    CELL4_RUN_ID,
    CONTINUATION_JOURNAL_SHA256,
    CONTINUATION_LOG_SHA256,
    EXPECTED_NEW_APPROVAL_SHA256,
    STOP_BUILD_ID,
    reconcile,
)
from m661_feasibility_collect import (
    CAP,
    EXPECTED_MANIFEST_SHA256,
    PROTOCOL_COMMIT,
    SOURCE_COMMIT,
    _sha256,
    _write_json,
)

SCOPE = "m661_feasibility_mode_only_cached_build_repair_after_cell4_no_paid_calls"


def _new_approval(path: Path, manifest: dict) -> dict:
    if EXPECTED_NEW_APPROVAL_SHA256 == "0" * 64:
        raise ValueError("cell-four repair remains closed pending exact human approval")
    if path.name != "cell4-continuation-approval.json" or path.is_symlink():
        raise ValueError("cell-four approval path differs")
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("cell-four approval is absent or not private")
    raw = path.read_bytes()
    if _sha256(raw) != EXPECTED_NEW_APPROVAL_SHA256:
        raise ValueError("cell-four approval bytes differ")
    approval = json.loads(raw)
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    if (
        approved_at.utcoffset() is None
        or approved_at <= datetime(2026, 10, 12, 9, 38, tzinfo=UTC)
        or approval.get("approved_by") != "human_user"
        or approval.get("approved_for_repair") is not True
        or approval.get("approved_for_paid_calls") is not False
        or approval.get("approval_scope") != SCOPE
        or approval.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("source_commit") != SOURCE_COMMIT
        or approval.get("protocol_commit") != PROTOCOL_COMMIT
        or approval.get("assignment_sha256") != manifest["schedule"]["cell_assignment_sha256"]
        or approval.get("stopped_continuation_journal_sha256") != CONTINUATION_JOURNAL_SHA256
        or approval.get("stopped_continuation_log_sha256") != CONTINUATION_LOG_SHA256
        or approval.get("retained_cell4_run_id") != str(CELL4_RUN_ID)
        or approval.get("cached_build_id") != str(STOP_BUILD_ID)
        or approval.get("paid_cells_authorized") != 0
        or approval.get("no_retry_or_replacement") is not True
        or approval.get("mode_only_cached_build_repair") is not True
        or Decimal(str(approval.get("cumulative_cost_ceiling_usd"))) != CAP
    ):
        raise ValueError("cell-four approval does not bind exact stopped scope")
    return approval


def _no_collector_running() -> None:
    output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or int(fields[0]) == os.getpid():
            continue
        command = fields[1]
        executable = command.split(None, 1)[0].rsplit("/", 1)[-1]
        if "python" not in executable:
            continue
        if any(
            name in command
            for name in (
                "m661_feasibility_collect.py",
                "m661_resume_after_cell1_permission.py",
                "m661_resume_after_cell4_permission.py",
            )
        ):
            raise ValueError("another M6.6.1 collector is running")


def _mode_plan(directory: Path, names: set[str]) -> dict[str, dict[str, str]]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("cached build directory is unsafe")
    if {path.name for path in directory.iterdir()} != names:
        raise ValueError("cached build file inventory differs")
    plan: dict[str, dict[str, str]] = {}
    for name in sorted(names):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("cached build file is unsafe")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != 0o600:
            raise ValueError("cached build file mode differs from stopped evidence")
        plan[name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "before": "0600",
            "after": "0644",
        }
    return plan


def _apply_mode_plan(directory: Path, plan: dict[str, dict[str, str]]) -> None:
    """Mutate file modes only; callers must first pass the repair approval gate."""
    for name, expected in plan.items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("cached build file became unsafe")
        if (
            stat.S_IMODE(path.stat().st_mode) != 0o600
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]
        ):
            raise ValueError("cached build changed after permission plan")
        os.chmod(path, 0o644, follow_symlinks=False)
    for name, expected in plan.items():
        path = directory / name
        if (
            stat.S_IMODE(path.stat().st_mode) != 0o644
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]
        ):
            raise ValueError("cached build permission repair changed bytes or mode")


async def repair_after_approval(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    new_approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    """Apply one exact, create-only repair after the separate approval is pinned."""
    # The zero pin rejects the proposal before it can touch original evidence.
    manifest = json.loads(manifest_path.read_bytes())
    _new_approval(new_approval_path, manifest)
    _no_collector_running()
    if new_approval_path.parent != approval_path.parent:
        raise ValueError("new approval is outside the private study directory")
    stopped = await reconcile(
        execution_root,
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
    )
    private = journal_path.parent
    intent = private / "cell4-cached-build-permission-intent.json"
    completion = private / "cell4-cached-build-permission-complete.json"
    if intent.exists() or completion.exists():
        raise ValueError("permission repair already attempted; reconcile separately")
    from offsecgym.runtime.compose import ComposeRangeRuntime

    state = ComposeRangeRuntime(state_dir).state
    build = state.verify_build_integrity(STOP_BUILD_ID)
    directory = state.build_dir(STOP_BUILD_ID)
    plan = _mode_plan(directory, set(build.artifact_digests) | {"manifest.json"})
    intent_sha = _write_json(
        intent,
        {
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "stopped_continuation_journal_sha256": stopped["continuation_journal_sha256"],
            "approval_sha256": EXPECTED_NEW_APPROVAL_SHA256,
            "build_id": str(STOP_BUILD_ID),
            "files": plan,
        },
    )
    _apply_mode_plan(directory, plan)
    state.verify_build_integrity(STOP_BUILD_ID)
    completion_sha = _write_json(
        completion,
        {
            "intent_sha256": intent_sha,
            "build_id": str(STOP_BUILD_ID),
            "file_count": len(plan),
            "bytes_unchanged": True,
            "new_file_mode": "0644",
        },
    )
    return {
        "intent_sha256": intent_sha,
        "completion_sha256": completion_sha,
        "file_count": len(plan),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "execution-root",
        "source-root",
        "protocol-root",
        "manifest",
        "approval",
        "new-approval",
        "journal",
        "state-dir",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(
        repair_after_approval(
            args.execution_root,
            args.source_root,
            args.protocol_root,
            args.manifest,
            args.approval,
            args.new_approval,
            args.journal,
            args.state_dir,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
