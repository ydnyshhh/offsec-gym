"""Read-only admission of the exact M6.6.1 interruption during cell 86.

The provider outcome of the second model call is unknown. This audit does not
settle its reservation, synthesize a terminal event, or authorize another call.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m661_cell1_recovery import STOP_EVENT_IDENTITY_SHA256, STOP_RUN_ID, STOP_TRACE_SHA256
from m661_cell1_recovery import STOP_JOURNAL_SHA256 as ORIGINAL_JOURNAL_SHA256
from m661_cell4_recovery_postcheck import _missing_trace, _new_records
from m661_cell4_stop import (
    CELL4_EVENT_IDENTITY_SHA256,
    CELL4_RUN_ID,
    CELL4_TRACE_SHA256,
    CONTINUATION_JOURNAL_SHA256,
    _stopped_records,
)
from m661_cell14_provider_stop import STOP_JOURNAL_SHA256 as CELL14_STOP_JOURNAL_SHA256
from m661_cell14_provider_stop import STOP_LOG_SHA256 as CELL14_STOP_LOG_SHA256
from m661_feasibility_collect import (
    CAP,
    CELL_RESERVATION,
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
from m661_resume_after_cell14_pause import _no_new_collector_running
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import (
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    RunCompleted,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import (
    action_reservations,
    model_reservations,
    run_usage,
    runs,
)
from offsecgym.storage.tables import (
    events as event_rows,
)

STOP_JOURNAL_SHA256 = "8d884cbef7d9f111b897461cca5d7233c81ae2eedec457b708af43b018e20335"
STOP_LOG_SHA256 = "05071c2f82661990be370e474cf0defc2759f9705ccec2615bfc15fa36864556"
PAIR42_SHA256 = "02047649c832207a9252da8e2fe2bc31be2b295df74bddc1ac74a9085df95a13"
PREFLIGHT43_SHA256 = "87e97d56c7f909ba97fd6a02517ba06a789509f43c998ca7d8727e87005c84b1"
INTERRUPTED_RUN_ID = UUID("829aef6a-c0b8-4d84-9ae7-2a45c7742ffd")
INTERRUPTED_SETUP_RUN_ID = UUID("22a49bad-37ec-4913-bd2b-8d0c5c6a54fd")
RETAINED_EARLY_SETUP_RUN_ID = UUID("52f176a6-d8d3-449f-b14f-2204c4b104f6")
INTERRUPTED_TRACE_SHA256 = "496c157ea502e70f197461d3b91aa8e56d9b8d3ef8b0c6232d30544797f2e794"
INTERRUPTED_IDENTITY_SHA256 = "7b02d9af85aa5000310aa0204b9d5ff005cc64602d46c998732532d4e05678ca"
UNSETTLED_CALL_ID = UUID("065635bf-1fff-4e15-90a6-8f68b8bed5e7")
SETTLED_CALL_ID = UUID("d94e77d9-96a0-49bc-85da-6bb464bd0c1b")
STOP_COMPLETED_ESTIMATED_COST = Decimal("35.417421")
STOP_COMPLETED_CONSERVATIVE_COST = Decimal("38.861571")
UNSETTLED_RESERVED_TOKENS = 20261
UNSETTLED_RESERVED_COST_MICROUSD = 159087
RECORDED_USED_COST_MICROUSD = 26463


def _completed_prefix(manifest: dict, private: Path) -> tuple[list[dict], dict]:
    path = private / "cell14-paid-continuation-journal.jsonl"
    if _sha256(path.read_bytes()) != STOP_JOURNAL_SHA256:
        raise ValueError("cell-86 stopped journal differs")
    lines = [json.loads(line) for line in path.read_bytes().splitlines()]
    if len(lines) != 144 or lines[-1].get("type") != "cell_started":
        raise ValueError("cell-86 journal must end with exactly one unmatched start")
    tail = lines[-1]
    cell = manifest["cells"][85]
    if (
        tail.get("order") != 86
        or tail.get("cell_id") != cell["cell_id"]
        or tail.get("run_id") != str(INTERRUPTED_RUN_ID)
    ):
        raise ValueError("interrupted assignment or source run differs")
    header = lines[0]
    if (
        header.get("type") != "batch_started"
        or header.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or header.get("expected_model_revision") != REVISION
        or header.get("expected_upstream_provider") != UPSTREAM
        or Decimal(str(header.get("max_estimated_usd"))) != CAP
    ):
        raise ValueError("cell-86 continuation header differs")
    records = []
    for order, (started, completed) in enumerate(
        zip(lines[1:-1:2], lines[2:-1:2], strict=True), 15
    ):
        assignment = manifest["cells"][order - 1]
        if (
            started.get("type") != "cell_started"
            or completed.get("type") != "cell_completed"
            or started.get("order") != order
            or completed.get("order") != order
            or started.get("cell_id") != assignment["cell_id"]
            or completed.get("cell_id") != assignment["cell_id"]
            or started.get("run_id") != completed.get("run_id")
            or completed.get("experiment_sha256") != assignment["experiment_sha256"]
        ):
            raise ValueError("completed prefix differs from frozen assignment order")
        records.append(completed)
    if len(records) != 71 or [r["order"] for r in records] != list(range(15, 86)):
        raise ValueError("completed prefix is not exactly cells 15–85")
    return records, tail


async def admit(
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
    expected_old = {
        "journal.jsonl": ORIGINAL_JOURNAL_SHA256,
        "continuation-journal.jsonl": CONTINUATION_JOURNAL_SHA256,
        "cell4-paid-continuation-journal.jsonl": CELL14_STOP_JOURNAL_SHA256,
        "collector-cell4-paid-session.log": CELL14_STOP_LOG_SHA256,
        "collector-cell14-session.log": STOP_LOG_SHA256,
        "pair-postcheck-42.json": PAIR42_SHA256,
        "endpoint-preflight-43.json": PREFLIGHT43_SHA256,
    }
    if any(
        _sha256((private / name).read_bytes()) != digest for name, digest in expected_old.items()
    ):
        raise ValueError("prior or stopped evidence bytes differ")
    if (private / "pair-postcheck-43.json").exists() or any(
        (private / f"endpoint-preflight-{number}.json").exists() for number in range(44, 49)
    ):
        raise ValueError("a later pair was attempted")
    _no_new_collector_running()
    old_records, _ = _stopped_records(manifest, private)
    first_records = _new_records(manifest, private / "cell4-paid-continuation-journal.jsonl")
    second_records, _ = _completed_prefix(manifest, private)
    records = [*old_records, *first_records, *second_records]
    if len(records) != 83 or len({r["run_id"] for r in records}) != 83:
        raise ValueError("retained source inventory differs")
    estimated = sum((Decimal(str(r["estimated_cost_usd"])) for r in records), Decimal())
    conservative = sum(
        (
            Decimal(str(r["estimated_cost_usd"])) if r["resource_auditable"] else CELL_RESERVATION
            for r in records
        ),
        Decimal(),
    )
    if (
        estimated != STOP_COMPLETED_ESTIMATED_COST
        or conservative != STOP_COMPLETED_CONSERVATIVE_COST
    ):
        raise ValueError("completed cost replay differs")
    endpoint = json.loads((private / "endpoint-preflight-43.json").read_bytes())
    if (
        endpoint.get("endpoint") != f"{UPSTREAM} | {REVISION}"
        or endpoint.get("input_usd_per_million_tokens") != 3.0
        or endpoint.get("output_usd_per_million_tokens") != 15.0
    ):
        raise ValueError("selected endpoint or price differs")
    import os

    url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not url:
        raise ValueError("dedicated database URL required")
    engine = create_async_engine(url)
    store = PostgresEventStore(engine)
    state = ComposeRangeRuntime(state_dir).state
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_cell86_stop_analysis",
    )
    try:
        await _missing_trace(
            store, STOP_RUN_ID, manifest["cells"][0], STOP_TRACE_SHA256, STOP_EVENT_IDENTITY_SHA256
        )
        await _missing_trace(
            store,
            CELL4_RUN_ID,
            manifest["cells"][3],
            CELL4_TRACE_SHA256,
            CELL4_EVENT_IDENTITY_SHA256,
        )
        audits = {
            record["order"]: await _audit_cell(
                store, state, analysis, manifest["cells"][record["order"] - 1], record, None
            )
            for record in records
        }
        if set(audits) != set(range(1, 86)) - {1, 4}:
            raise ValueError("completed assignment inventory differs")
        replay_ids = set()
        for pair_number in range(1, 43):
            receipt = json.loads((private / f"pair-postcheck-{pair_number}.json").read_bytes())
            pair_orders = (2 * pair_number - 1, 2 * pair_number)
            if (
                receipt.get("pair_number") != pair_number
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("cell_ids")
                != [manifest["cells"][order - 1]["cell_id"] for order in pair_orders]
            ):
                raise ValueError("retained pair receipt differs")
            for offset, order in enumerate(pair_orders):
                if order in audits and receipt["audits"][offset] != audits[order]:
                    raise ValueError("retained pair audit differs from PostgreSQL replay")
            replay_ids.update(
                UUID(run_id)
                for audit in receipt["audits"]
                for run_id in audit.get("validator_replay_run_ids", [])
            )
        if len(list(private.glob("pair-postcheck-*.json"))) != 42:
            raise ValueError("pair receipt inventory differs")
        if len(list(private.glob("endpoint-preflight-*.json"))) != 43:
            raise ValueError("endpoint preflight inventory differs")
        if (
            len(list((private / "stages").glob("*.json"))) != 83
            or len(list((private / "traces").glob("*.json"))) != 83
        ):
            raise ValueError("stage or source trace artifact inventory differs")
        source_trace = await store.read_run(INTERRUPTED_RUN_ID)
        setup_trace = await store.read_run(INTERRUPTED_SETUP_RUN_ID)
        if (
            len(source_trace) != 674
            or _trace_sha(source_trace) != INTERRUPTED_TRACE_SHA256
            or _identity_sha(source_trace) != INTERRUPTED_IDENTITY_SHA256
            or len([e for e in source_trace if isinstance(e, ModelCallStarted)]) != 2
            or len([e for e in source_trace if isinstance(e, ModelCallCompleted)]) != 1
            or any(isinstance(e, (ModelCallFailed, RunCompleted)) for e in source_trace)
            or len(setup_trace) != 34
            or [e.status for e in setup_trace if isinstance(e, RunCompleted)] != ["completed"]
        ):
            raise ValueError("interrupted source or setup trace differs")
        async with engine.connect() as connection:
            actual = set((await connection.execute(select(runs.c.run_id))).scalars().all())
            total_events = (
                await connection.execute(select(func.count()).select_from(event_rows))
            ).scalar_one()
            actions = (
                await connection.execute(
                    select(func.count())
                    .select_from(action_reservations)
                    .where(action_reservations.c.released_at.is_(None))
                )
            ).scalar_one()
            reservations = (
                (
                    await connection.execute(
                        select(model_reservations).where(model_reservations.c.settled_at.is_(None))
                    )
                )
                .mappings()
                .all()
            )
            reserved_usage = (
                await connection.execute(
                    select(
                        run_usage.c.run_id,
                        run_usage.c.reserved_tokens,
                        run_usage.c.reserved_cost_microusd,
                    ).where(
                        (run_usage.c.reserved_tokens != 0)
                        | (run_usage.c.reserved_cost_microusd != 0)
                    )
                )
            ).all()
            usage = (
                (
                    await connection.execute(
                        select(run_usage).where(run_usage.c.run_id == INTERRUPTED_RUN_ID)
                    )
                )
                .mappings()
                .one()
            )
        expected_runs = {
            STOP_RUN_ID,
            CELL4_RUN_ID,
            RETAINED_EARLY_SETUP_RUN_ID,
            INTERRUPTED_RUN_ID,
            INTERRUPTED_SETUP_RUN_ID,
            *(UUID(r[key]) for r in records for key in ("run_id", "setup_run_id")),
            *replay_ids,
        }
        if (
            actual != expected_runs
            or len(actual) != 170
            or total_events != 136601
            or actions != 0
            or len(reservations) != 1
            or reservations[0]["run_id"] != INTERRUPTED_RUN_ID
            or reservations[0]["call_id"] != UNSETTLED_CALL_ID
            or reservations[0]["worker_id"] is not None
            or reservations[0]["reserved_tokens"] != UNSETTLED_RESERVED_TOKENS
            or reservations[0]["reserved_cost_microusd"] != UNSETTLED_RESERVED_COST_MICROUSD
            or reserved_usage
            != [(INTERRUPTED_RUN_ID, UNSETTLED_RESERVED_TOKENS, UNSETTLED_RESERVED_COST_MICROUSD)]
            or usage["used_model_calls"] != 2
            or usage["used_cost_microusd"] != RECORDED_USED_COST_MICROUSD
        ):
            raise ValueError("dedicated PostgreSQL inventory or reservation differs")
        if conservative + CELL_RESERVATION * 11 > CAP:
            raise ValueError("remaining assignments exceed original cumulative cap")
        return {
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "stopped_journal_sha256": STOP_JOURNAL_SHA256,
            "stopped_log_sha256": STOP_LOG_SHA256,
            "completed_assigned_cells": 85,
            "interrupted_cell_order": 86,
            "first_unstarted_order": 87,
            "retained_missing_orders": [1, 4, 86],
            "retained_provider_failed_orders": [13, 14],
            "completed_estimated_cost_usd": str(estimated),
            "completed_conservative_cost_usd": str(conservative),
            "interrupted_cell_conservative_reservation_usd": str(CELL_RESERVATION),
            "active_model_reservations": 1,
            "paid_calls_authorized": False,
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
