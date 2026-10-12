"""Read-only admission of the stopped M6.6.1 cell-4 infrastructure failure.

This is a forensic gate for a *proposed* continuation. It never repairs a
build, writes study evidence, or calls a model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m661_cell1_recovery import STOP_BUILD_ID, STOP_RUN_ID
from m661_cell1_recovery import reconcile as reconcile_cell1
from m661_feasibility_collect import (
    CAP,
    EXPECTED_MANIFEST_SHA256,
    REVISION,
    UPSTREAM,
    _approval_and_manifest,
    _audit_cell,
    _identity_sha,
    _load_module,
    _sha256,
    _trace_sha,
)
from m661_resume_after_cell1_permission import _scope_approval
from sqlalchemy import func, select
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
from offsecgym.storage.tables import action_reservations, model_reservations, run_usage, runs

CONTINUATION_JOURNAL_SHA256 = "abcf535e29ffa26b24feb6c5eed02534f60253eada9fa4676992853d3a191077"
CONTINUATION_LOG_SHA256 = "cf2a2575dcf06b4776b3082e0c0396e16b990f74031030bb1c93b89c01d92b87"
CELL4_RUN_ID = UUID("52f176a6-d8d3-449f-b14f-2204c4b104f6")
CELL4_TRACE_SHA256 = "2f5f4fe2ce5348b3fa4a5feb10b13e714029721de86a3a7da0919de4e1116564"
CELL4_EVENT_IDENTITY_SHA256 = "c69e40e8aca18c5fe1dd7f56825d7f70bd61aa114f62aaba40a21d81e00a6b80"
CELL4_INSTANCE_ID = UUID("3daad169-387a-4485-886a-ca85fc5f5bc5")
EXPECTED_NEW_APPROVAL_SHA256 = "0" * 64


def _stopped_records(manifest: dict, private: Path) -> tuple[list[dict], dict]:
    if (
        _sha256((private / "collector-cell1-continuation.log").read_bytes())
        != CONTINUATION_LOG_SHA256
    ):
        raise ValueError("stopped continuation log differs")
    path = private / "continuation-journal.jsonl"
    if path.is_symlink() or _sha256(path.read_bytes()) != CONTINUATION_JOURNAL_SHA256:
        raise ValueError("stopped continuation journal differs")
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if len(rows) != 6:
        raise ValueError("expected exactly two completed cells and one started cell")
    header = rows[0]
    if any(
        header.get(key) != value
        for key, value in (
            ("type", "batch_started"),
            ("manifest_sha256", EXPECTED_MANIFEST_SHA256),
            ("expected_model_revision", REVISION),
            ("expected_upstream_provider", UPSTREAM),
            ("max_estimated_usd", float(CAP)),
        )
    ):
        raise ValueError("continuation header differs")
    records: list[dict] = []
    for order, start, record in ((2, rows[1], rows[2]), (3, rows[3], rows[4])):
        cell = manifest["cells"][order - 1]
        if (
            start.get("type") != "cell_started"
            or record.get("type") != "cell_completed"
            or any(
                item.get("order") != order or item.get("cell_id") != cell["cell_id"]
                for item in (start, record)
            )
            or start.get("run_id") != record.get("run_id")
            or record.get("experiment_sha256") != cell["experiment_sha256"]
        ):
            raise ValueError("completed prefix differs from frozen assignments")
        records.append(record)
    fourth = manifest["cells"][3]
    started = rows[5]
    if (
        started.get("type") != "cell_started"
        or started.get("order") != 4
        or started.get("cell_id") != fourth["cell_id"]
        or started.get("run_id") != str(CELL4_RUN_ID)
    ):
        raise ValueError("cell four start differs from frozen assignment")
    if len({str(STOP_RUN_ID), *(record["run_id"] for record in records), started["run_id"]}) != 4:
        raise ValueError("source run identity repeated")
    return records, started


def _cached_build_modes(state, manifest: dict) -> dict:
    old = state.verify_build_integrity(STOP_BUILD_ID)
    fourth = manifest["cells"][3]
    if old.spec.seed != fourth["seed"] or not old.spec.patched:
        raise ValueError("cached build differs from cell-four assignment")
    directory = state.build_dir(STOP_BUILD_ID)
    if directory.is_symlink() or any(path.is_symlink() for path in directory.iterdir()):
        raise ValueError("cached build contains an unsafe path")
    modes = {path.name: stat.S_IMODE(path.stat().st_mode) for path in directory.iterdir()}
    if set(modes) != set(old.artifact_digests) | {"manifest.json"} or set(modes.values()) != {
        0o600
    }:
        raise ValueError("cached build file modes differ from stopped evidence")
    instance = state.load_instance(CELL4_INSTANCE_ID)
    if instance.build_id != STOP_BUILD_ID or instance.state != "destroyed":
        raise ValueError("cell-four range instance differs")
    return {"build_id": str(STOP_BUILD_ID), "file_count": len(modes), "old_file_mode": "0600"}


async def reconcile(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, _ = _approval_and_manifest(
        execution_root, source_root, protocol_root, manifest_path, approval_path
    )
    private = approval_path.parent
    if journal_path != private / "journal.jsonl" or state_dir != private / "range-state":
        raise ValueError("private study paths differ")
    first = await reconcile_cell1(
        execution_root,
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
        allow_continuation=True,
    )
    _scope_approval(private / "cell1-continuation-approval.json", manifest)
    records, _ = _stopped_records(manifest, private)
    state = ComposeRangeRuntime(state_dir).state
    build_modes = _cached_build_modes(state, manifest)
    if (state_dir / "critical_checkpoints" / f"{CELL4_RUN_ID.hex}.json").exists():
        raise ValueError("cell-four checkpoint receipt unexpectedly exists")
    if (private / "pair-postcheck-2.json").exists() or (
        private / "stages" / f"{manifest['cells'][3]['cell_id']}.json"
    ).exists():
        raise ValueError("cell-four pair or stage unexpectedly exists")
    if len(list(private.glob("pair-postcheck-*.json"))) != 1:
        raise ValueError("pair receipt inventory differs")
    if (
        len(list((private / "stages").glob("*.json"))) != 2
        or len(list((private / "traces").glob("*.json"))) != 2
    ):
        raise ValueError("saved stage or trace inventory differs")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("dedicated database URL required")
    engine = create_async_engine(database_url)
    events = PostgresEventStore(engine)
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_cell4_stop_analysis",
    )
    try:
        audits = [
            await _audit_cell(events, state, analysis, manifest["cells"][index + 1], record, None)
            for index, record in enumerate(records)
        ]
        trace = await events.read_run(CELL4_RUN_ID)
        if (
            len(trace) != 2
            or [item.sequence_number for item in trace] != [1, 2]
            or [type(item) for item in trace] != [RunStarted, RunCompleted]
            or any(item.run_id != CELL4_RUN_ID for item in trace)
            or len({item.event_id for item in trace}) != 2
            or trace[0].experiment_hash != manifest["cells"][3]["experiment_sha256"]
            or trace[1].status != "environment_failed"
            or _trace_sha(trace) != CELL4_TRACE_SHA256
            or _identity_sha(trace) != CELL4_EVENT_IDENTITY_SHA256
            or any(
                isinstance(item, (ModelCallStarted, ModelCallCompleted, ModelCallFailed))
                for item in trace
            )
        ):
            raise ValueError("cell-four authoritative source trace differs")
        projection = project_controller_events(trace)
        if (
            projection.active_actions
            or projection.model_reservations
            or projection.active_workers
            or any(hold.active for hold in projection.admission_holds.values())
        ):
            raise ValueError("cell-four source retained a reservation")
        source_ids = {STOP_RUN_ID, CELL4_RUN_ID, *(UUID(record["run_id"]) for record in records)}
        setup_ids = {UUID(record["setup_run_id"]) for record in records}
        replay_ids = {
            UUID(value) for audit in audits for value in audit["validator_replay_run_ids"]
        }
        if source_ids & setup_ids or source_ids & replay_ids or setup_ids & replay_ids:
            raise ValueError("source, setup, or replay run identity overlaps")
        async with engine.connect() as connection:
            actual = set((await connection.execute(select(runs.c.run_id))).scalars().all())
            actions = (
                await connection.execute(
                    select(func.count())
                    .select_from(action_reservations)
                    .where(action_reservations.c.released_at.is_(None))
                )
            ).scalar_one()
            models = (
                await connection.execute(
                    select(func.count())
                    .select_from(model_reservations)
                    .where(model_reservations.c.settled_at.is_(None))
                )
            ).scalar_one()
            usage = (
                await connection.execute(
                    select(run_usage.c.reserved_tokens, run_usage.c.reserved_cost_microusd)
                )
            ).all()
        if (
            actual != source_ids | setup_ids | replay_ids
            or actions
            or models
            or any(tokens or cost for tokens, cost in usage)
        ):
            raise ValueError("dedicated database has extra work or retained reservation")
        for replay_id in replay_ids:
            replay = await events.read_run(replay_id)
            if not replay or any(
                isinstance(item, (ModelCallStarted, ModelCallCompleted)) for item in replay
            ):
                raise ValueError("validator replay contains model work")
        cost = sum((Decimal(str(record["estimated_cost_usd"])) for record in records), Decimal(0))
        if cost != Decimal("0.719841") or cost > CAP:
            raise ValueError("stopped cumulative cost differs")
        return {
            "protocol": manifest["protocol"],
            "assigned_cells_started": 4,
            "completed_continuation_cells": 2,
            "retained_infrastructure_missing_cells": [1, 4],
            "next_unstarted_order": 5,
            "original_cell1": first,
            "cell4_run_id": str(CELL4_RUN_ID),
            "cell4_event_count": len(trace),
            "cell4_terminal_status": "environment_failed",
            "cell4_model_calls": 0,
            "cell4_trace_sha256": CELL4_TRACE_SHA256,
            "cell4_event_identity_sha256": CELL4_EVENT_IDENTITY_SHA256,
            "completed_audits": audits,
            "cached_build": build_modes,
            "dedicated_run_count": len(actual),
            "cumulative_estimated_cost_usd": str(cost),
            "continuation_journal_sha256": CONTINUATION_JOURNAL_SHA256,
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
    print(
        json.dumps(
            asyncio.run(
                reconcile(
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
