"""Fail-closed, offline reconciliation of the stopped M6.6.1 first cell.

This module performs no model call and makes no study-evidence write.  A later
continuation must consume a separate, exact-scope human approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from m661_feasibility_collect import (
    EXPECTED_MANIFEST_SHA256,
    REVISION,
    UPSTREAM,
    _approval_and_manifest,
    _identity_sha,
    _sha256,
    _trace_sha,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import (
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events
from offsecgym.storage.tables import runs

STOP_JOURNAL_SHA256 = "3c763b636487e39b25fd921570e0ec9d3ab45d18eae10fdd7e14d2d53fb026ae"
STOP_LOG_SHA256 = "c576d54e56ec0ab18d5650792fcba78b39d21eee9a6fd996064b8811470c5dae"
PAIR1_ENDPOINT_SHA256 = "8abaf957484a22ac56262fe49eac4db8a706b759aa4a5469ef914aec8ac8f211"
STOP_RUN_ID = UUID("b23fffa6-62bc-4aec-a6a3-f1492d4d4445")
STOP_TRACE_SHA256 = "800bbd11ddef09f688340fc3d106fddf73c2f8e1338e6ae13b71b6c5f39fd846"
STOP_EVENT_IDENTITY_SHA256 = "aabfb7617981f672f2d7e21e1b663660cae457c685d691f8ecfdd6c91180a206"
STOP_BUILD_ID = UUID("1a63d7c3-7b21-588a-bc1d-9a4e080e87e3")
STOP_INSTANCE_ID = UUID("a3e6d0d8-246e-4904-af58-b596919dd62f")
EXPECTED_CONTINUATION_APPROVAL_SHA256 = (
    "5b5f7604f5930054eafb6dba6b3e69f169a1386b197d127e624220b1cf5d2860"
)


def _private_paths(approval_path: Path, journal_path: Path, state_dir: Path) -> None:
    parent = approval_path.parent
    if (
        approval_path.name != "approval.json"
        or journal_path != parent / "journal.jsonl"
        or state_dir != parent / "range-state"
        or journal_path.is_symlink()
        or state_dir.is_symlink()
        or not journal_path.is_file()
        or not state_dir.is_dir()
    ):
        raise ValueError("private stopped-study paths differ")


def _stopped_journal(manifest: dict, journal_path: Path) -> dict:
    raw = journal_path.read_bytes()
    if _sha256(raw) != STOP_JOURNAL_SHA256:
        raise ValueError("stopped journal bytes differ")
    lines = [json.loads(line) for line in raw.splitlines()]
    if len(lines) != 2:
        raise ValueError("stopped journal does not contain exactly one started cell")
    header, start = lines
    first = manifest["cells"][0]
    if (
        header.get("type") != "batch_started"
        or header.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or header.get("expected_model_revision") != REVISION
        or header.get("expected_upstream_provider") != UPSTREAM
        or header.get("max_estimated_usd") != 172.8
        or start.get("type") != "cell_started"
        or start.get("order") != 1
        or start.get("cell_id") != first["cell_id"]
        or start.get("run_id") != str(STOP_RUN_ID)
    ):
        raise ValueError("stopped journal header or first assignment differs")
    return first


def _local_evidence(
    manifest: dict,
    private_dir: Path,
    state_dir: Path,
    *,
    allow_continuation: bool,
    allow_repaired_build: bool = False,
) -> dict:
    if _sha256((private_dir / "collector.log").read_bytes()) != STOP_LOG_SHA256:
        raise ValueError("stopped collector log differs")
    endpoint_raw = (private_dir / "endpoint-preflight-1.json").read_bytes()
    endpoint = json.loads(endpoint_raw)
    if (
        _sha256(endpoint_raw) != PAIR1_ENDPOINT_SHA256
        or endpoint.get("endpoint") != f"{UPSTREAM} | {REVISION}"
        or endpoint.get("input_usd_per_million_tokens") != 3.0
        or endpoint.get("output_usd_per_million_tokens") != 15.0
    ):
        raise ValueError("original pair-one endpoint evidence differs")
    if (private_dir / "stages" / f"{manifest['cells'][0]['cell_id']}.json").exists():
        raise ValueError("first cell unexpectedly has a checkpoint stage artifact")
    if not allow_continuation and (
        any(private_dir.glob("pair-postcheck-*.json")) or any(private_dir.glob("stages/*.json"))
    ):
        raise ValueError("stopped prefix unexpectedly has a pair or stage artifact")
    state = ComposeRangeRuntime(state_dir).state
    builds = {item.name for item in (state_dir / "builds").iterdir()}
    instances = {item.name for item in (state_dir / "instances").iterdir()}
    if (
        (STOP_BUILD_ID.hex not in builds or STOP_INSTANCE_ID.hex not in instances)
        if allow_continuation
        else (builds != {STOP_BUILD_ID.hex} or instances != {STOP_INSTANCE_ID.hex})
    ):
        raise ValueError("stopped range build or instance inventory differs")
    build = state.verify_build_integrity(STOP_BUILD_ID)
    instance = state.load_instance(STOP_INSTANCE_ID)
    first = manifest["cells"][0]
    if (
        build.spec.seed != first["seed"]
        or build.spec.patched != (first["variant"] == "patched")
        or instance.build_id != STOP_BUILD_ID
        or instance.state != "destroyed"
    ):
        raise ValueError("stopped range state differs from the first assignment")
    init_script = state.build_dir(STOP_BUILD_ID) / "init_db.py"
    mode = init_script.stat().st_mode & 0o777
    if mode != (0o644 if allow_repaired_build else 0o600):
        raise ValueError("cached init script permissions differ from expected evidence")
    return {"endpoint_preflight_sha256": PAIR1_ENDPOINT_SHA256, "build_id": str(STOP_BUILD_ID)}


async def reconcile(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
    *,
    allow_continuation: bool = False,
    allow_repaired_build: bool = False,
) -> dict:
    manifest, _ = _approval_and_manifest(
        execution_root, source_root, protocol_root, manifest_path, approval_path
    )
    _private_paths(approval_path, journal_path, state_dir)
    first = _stopped_journal(manifest, journal_path)
    local = _local_evidence(
        manifest,
        journal_path.parent,
        state_dir,
        allow_continuation=allow_continuation,
        allow_repaired_build=allow_repaired_build,
    )
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("dedicated PostgreSQL URL is required")
    engine = create_async_engine(database_url)
    events = PostgresEventStore(engine)
    try:
        async with engine.connect() as connection:
            run_ids = set((await connection.execute(select(runs.c.run_id))).scalars().all())
        if STOP_RUN_ID not in run_ids if allow_continuation else run_ids != {STOP_RUN_ID}:
            raise ValueError("dedicated PostgreSQL contains an extra or missing run")
        trace = await events.read_run(STOP_RUN_ID)
        if (
            len(trace) != 2
            or [event.sequence_number for event in trace] != [1, 2]
            or any(event.run_id != STOP_RUN_ID for event in trace)
            or len({event.event_id for event in trace}) != 2
            or [type(event) for event in trace] != [RunStarted, RunCompleted]
            or trace[0].experiment_hash != first["experiment_sha256"]
            or trace[1].status != "environment_failed"
            or _trace_sha(trace) != STOP_TRACE_SHA256
            or _identity_sha(trace) != STOP_EVENT_IDENTITY_SHA256
            or any(
                isinstance(event, (ModelCallStarted, ModelCallCompleted, ModelCallFailed))
                for event in trace
            )
        ):
            raise ValueError("first-cell authoritative event trace differs")
        projection = project_controller_events(trace)
        if (
            projection.active_actions
            or projection.model_reservations
            or projection.active_workers
            or any(hold.active for hold in projection.admission_holds.values())
        ):
            raise ValueError("first-cell controller retained an active reservation")
        return {
            "protocol": manifest["protocol"],
            "cell_id": first["cell_id"],
            "run_id": str(STOP_RUN_ID),
            "terminal_status": "environment_failed",
            "score_valid": False,
            "trace_auditable": False,
            "model_call_starts": 0,
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_model_token_cost_usd": "0",
            "event_count": 2,
            "trace_sha256": STOP_TRACE_SHA256,
            "event_identity_sha256": STOP_EVENT_IDENTITY_SHA256,
            "stopped_journal_sha256": STOP_JOURNAL_SHA256,
            **local,
        }
    finally:
        await engine.dispose()


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
    result = asyncio.run(
        reconcile(
            args.execution_root,
            args.source_root,
            args.protocol_root,
            args.manifest,
            args.approval,
            args.journal,
            args.state_dir,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
