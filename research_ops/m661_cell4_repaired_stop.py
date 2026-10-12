"""Read-only admission of the exact cell-4 stop after approved mode repair.

This checks the original six-run stop and the byte-preserving repair before any
new approval or model call. It never writes study evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import stat
from pathlib import Path

from m661_cached_build_permissions import _new_approval
from m661_cell4_stop import (
    CELL4_EVENT_IDENTITY_SHA256,
    CELL4_RUN_ID,
    CELL4_TRACE_SHA256,
    CONTINUATION_JOURNAL_SHA256,
    EXPECTED_NEW_APPROVAL_SHA256,
    STOP_BUILD_ID,
    reconcile,
)
from m661_feasibility_collect import EXPECTED_MANIFEST_SHA256, _sha256

from offsecgym.runtime.compose import ComposeRangeRuntime

INTENT_SHA256 = "fbc1c2721e030f90ac3954f3ff1ed573fefc8404b96f3eb79628d138caf563f5"
COMPLETION_SHA256 = "2e353af79f9e3ccdfd9e04dbfea169a9584635fb62afc230146150afa8e94347"


def retained_four_pair_audit(cell: dict) -> dict:
    """Exact, independent representation of the unscored fourth assignment."""
    return {
        "cell_id": cell["cell_id"],
        "run_id": str(CELL4_RUN_ID),
        "status": "environment_failed",
        "score_valid": False,
        "trace_auditable": False,
        "resource_auditable": True,
        "model_calls": 0,
        "estimated_cost_usd": "0",
        "trace_sha256": CELL4_TRACE_SHA256,
        "event_identity_sha256": CELL4_EVENT_IDENTITY_SHA256,
        "missing_reason": "infrastructure_permission_failure_before_checkpoint",
    }


def _repair_evidence(private: Path, state_dir: Path) -> dict:
    intent_path = private / "cell4-cached-build-permission-intent.json"
    completion_path = private / "cell4-cached-build-permission-complete.json"
    if intent_path.is_symlink() or completion_path.is_symlink():
        raise ValueError("repair evidence path is unsafe")
    intent_raw, completion_raw = intent_path.read_bytes(), completion_path.read_bytes()
    if _sha256(intent_raw) != INTENT_SHA256 or _sha256(completion_raw) != COMPLETION_SHA256:
        raise ValueError("repair evidence bytes differ")
    intent, completion = json.loads(intent_raw), json.loads(completion_raw)
    files = intent.get("files")
    state = ComposeRangeRuntime(state_dir).state
    build = state.verify_build_integrity(STOP_BUILD_ID)
    directory = state.build_dir(STOP_BUILD_ID)
    if (
        intent.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or intent.get("stopped_continuation_journal_sha256") != CONTINUATION_JOURNAL_SHA256
        or intent.get("approval_sha256") != EXPECTED_NEW_APPROVAL_SHA256
        or intent.get("build_id") != str(STOP_BUILD_ID)
        or not isinstance(files, dict)
        or set(files) != set(build.artifact_digests) | {"manifest.json"}
        or completion
        != {
            "intent_sha256": INTENT_SHA256,
            "build_id": str(STOP_BUILD_ID),
            "file_count": len(files),
            "bytes_unchanged": True,
            "new_file_mode": "0644",
        }
    ):
        raise ValueError("repair intent or completion scope differs")
    if directory.is_symlink() or {p.name for p in directory.iterdir()} != set(files):
        raise ValueError("repaired build inventory differs")
    for name, entry in files.items():
        path = directory / name
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o644
            or _sha256(path.read_bytes()) != entry.get("sha256")
            or entry.get("before") != "0600"
            or entry.get("after") != "0644"
        ):
            raise ValueError("repaired build bytes or mode differ")
    return {"intent_sha256": INTENT_SHA256, "completion_sha256": COMPLETION_SHA256}


async def admit(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    stopped = await reconcile(
        execution_root,
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
        repaired=True,
    )
    _new_approval(
        approval_path.parent / "cell4-continuation-approval.json",
        json.loads(manifest_path.read_bytes()),
    )
    repair = _repair_evidence(approval_path.parent, state_dir)
    private = approval_path.parent
    if (
        (private / "cell4-paid-continuation-journal.jsonl").exists()
        or (private / "cell4-paid-clearance.json").exists()
        or (private / "pair-postcheck-2.json").exists()
    ):
        raise ValueError("cell-four paid continuation already attempted")
    return {**stopped, "repair": repair}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "execution-root",
        "source-root",
        "protocol-root",
        "manifest",
        "approval",
        "journal",
        "state-dir",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                admit(
                    args.execution_root,
                    args.source_root,
                    args.protocol_root,
                    args.manifest,
                    args.approval,
                    args.journal,
                    args.state_dir,
                )
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
