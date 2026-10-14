"""Read-only admission of the exact M6.6.1 stop during frozen cell 92.

Cell 91's provider failure and cell 92's unfinished action are retained. This
module never releases an action, settles a model reservation, or calls a model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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
from m661_cell86_interrupted_stop import (
    INTERRUPTED_IDENTITY_SHA256 as CELL86_IDENTITY_SHA256,
)
from m661_cell86_interrupted_stop import (
    INTERRUPTED_RUN_ID as CELL86_RUN_ID,
)
from m661_cell86_interrupted_stop import (
    INTERRUPTED_SETUP_RUN_ID as CELL86_SETUP_RUN_ID,
)
from m661_cell86_interrupted_stop import (
    INTERRUPTED_TRACE_SHA256 as CELL86_TRACE_SHA256,
)
from m661_cell86_interrupted_stop import (
    RECORDED_USED_COST_MICROUSD,
    RETAINED_EARLY_SETUP_RUN_ID,
    UNSETTLED_CALL_ID,
    UNSETTLED_RESERVED_COST_MICROUSD,
    UNSETTLED_RESERVED_TOKENS,
    _completed_prefix,
)
from m661_cell86_interrupted_stop import (
    STOP_JOURNAL_SHA256 as CELL86_STOP_JOURNAL_SHA256,
)
from m661_cell86_interrupted_stop import (
    STOP_LOG_SHA256 as CELL86_STOP_LOG_SHA256,
)
from m661_cell86_recovery_postcheck import _file_set_digest, interrupted_pair_audit
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
from m661_resume_after_cell86_permission import _no_collector_running, _scope_approval
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import ModelCallStarted, RunCompleted, RunStarted
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import (
    action_reservations,
    events,
    model_reservations,
    run_usage,
    runs,
)

STOP_JOURNAL_SHA256 = "c205ec34ffdc442005cbfea0968fb2c13acbbcfdc57e2fd95f7673d1734e612f"
STOP_LOG_SHA256 = "b37a654129e035aa4ceccf53ab4055ae11a7b933e088dd2678885539e3c12282"
PAIR45_SHA256 = "018ca081ed6fe245fd8cc69a1f4494005e576809481c51c8099e8de07799f7c7"
PREFLIGHT46_SHA256 = "8bc0e5840ecd74d51a7c67dee087634109600356f2c1baea8d6e64d3ef806400"
PRIOR_PAIRS_DIGEST = "fa2c141f2205b94c44fb2d5baddc7c4b3079f3d0f8fc7346a0bf9b9ac6a1c3b4"
PRIOR_PREFLIGHTS_DIGEST = "2433227ce3e4f8cf57f3a4db72a2fab7c36df4721e1f2d442b2cee14e8d83ecc"
INTERRUPTED_RUN_ID = UUID("29d70b52-e385-4695-bd97-af2ad29e2c69")
INTERRUPTED_TRACE_SHA256 = "53cee125d8eb556c957e722bb3747a5608d6d78ab2558c538bc0660a661a6d2e"
INTERRUPTED_IDENTITY_SHA256 = "066c198473372d1bd9e38d3f9237ee25e3dc1ace45ce2bc1285f14268a54de2a"
ACTIVE_ACTION_ID = UUID("78843573-fe7e-4023-908d-a54d69b2895d")
TOTAL_RUNS = 181
TOTAL_EVENTS = 144702
COMPLETED_ESTIMATED_COST = Decimal("37.281219")
CONSERVATIVE_WITH_MISSING = Decimal("45.996150")


def _stopped_third_records(manifest: dict, private: Path) -> tuple[list[dict], dict]:
    path = private / "cell86-paid-continuation-journal.jsonl"
    if _sha256(path.read_bytes()) != STOP_JOURNAL_SHA256:
        raise ValueError("cell-92 stopped journal differs")
    lines = [json.loads(line) for line in path.read_bytes().splitlines()]
    if len(lines) != 12 or lines[-1].get("type") != "cell_started":
        raise ValueError("cell-92 journal must end with one unmatched start")
    header, tail = lines[0], lines[-1]
    if (
        header.get("type") != "batch_started"
        or header.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or header.get("expected_model_revision") != REVISION
        or header.get("expected_upstream_provider") != UPSTREAM
        or Decimal(str(header.get("max_estimated_usd"))) != CAP
        or tail.get("order") != 92
        or tail.get("cell_id") != manifest["cells"][91]["cell_id"]
        or tail.get("run_id") != str(INTERRUPTED_RUN_ID)
    ):
        raise ValueError("cell-92 header or assignment differs")
    records = []
    for order, (started, completed) in enumerate(
        zip(lines[1:-1:2], lines[2:-1:2], strict=True), 87
    ):
        cell = manifest["cells"][order - 1]
        if (
            started.get("type") != "cell_started"
            or completed.get("type") != "cell_completed"
            or started.get("order") != order
            or completed.get("order") != order
            or started.get("cell_id") != cell["cell_id"]
            or completed.get("cell_id") != cell["cell_id"]
            or started.get("run_id") != completed.get("run_id")
            or completed.get("experiment_sha256") != cell["experiment_sha256"]
        ):
            raise ValueError("cell-92 completed prefix differs from assignment")
        records.append(completed)
    if [record["order"] for record in records] != list(range(87, 92)):
        raise ValueError("completed prefix must be exactly cells 87–91")
    if (
        records[-1]["status"] != "provider_failed"
        or records[-1]["failure_reason"] != "provider_unavailable"
    ):
        raise ValueError("cell 91 provider failure differs")
    if records[-1]["score_valid"] or records[-1]["resource_auditable"]:
        raise ValueError("cell 91 must remain score-invalid and resource-unauditable")
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
    _scope_approval(private / "cell86-paid-continuation-approval.json", manifest)
    pinned = {
        "journal.jsonl": ORIGINAL_JOURNAL_SHA256,
        "continuation-journal.jsonl": CONTINUATION_JOURNAL_SHA256,
        "cell4-paid-continuation-journal.jsonl": CELL14_STOP_JOURNAL_SHA256,
        "collector-cell4-paid-session.log": CELL14_STOP_LOG_SHA256,
        "cell14-paid-continuation-journal.jsonl": CELL86_STOP_JOURNAL_SHA256,
        "collector-cell14-session.log": CELL86_STOP_LOG_SHA256,
        "cell86-paid-continuation-journal.jsonl": STOP_JOURNAL_SHA256,
        "collector-cell86-session.log": STOP_LOG_SHA256,
        "pair-postcheck-45.json": PAIR45_SHA256,
        "endpoint-preflight-46.json": PREFLIGHT46_SHA256,
    }
    if any(_sha256((private / name).read_bytes()) != digest for name, digest in pinned.items()):
        raise ValueError("stopped or prior evidence bytes differ")
    if (
        _file_set_digest([private / f"pair-postcheck-{i}.json" for i in range(1, 46)])
        != PRIOR_PAIRS_DIGEST
        or _file_set_digest([private / f"endpoint-preflight-{i}.json" for i in range(1, 47)])
        != PRIOR_PREFLIGHTS_DIGEST
    ):
        raise ValueError("pair receipt or endpoint history changed")
    if any((private / f"pair-postcheck-{i}.json").exists() for i in range(46, 49)):
        raise ValueError("pair 46 or later already closed")
    if any((private / f"endpoint-preflight-{i}.json").exists() for i in range(47, 49)):
        raise ValueError("later endpoint preflight exists")
    if len(list((private / "stages").glob("*.json"))) != 88 or len(
        list((private / "traces").glob("*.json"))
    ) != 88:
        raise ValueError("stopped stage or trace inventory differs")
    _no_collector_running()
    old, _ = _stopped_records(manifest, private)
    first = _new_records(manifest, private / "cell4-paid-continuation-journal.jsonl")
    second, _ = _completed_prefix(manifest, private)
    third, _ = _stopped_third_records(manifest, private)
    records = [*old, *first, *second, *third]
    if len(records) != 88 or len({r["run_id"] for r in records}) != 88:
        raise ValueError("retained completed source inventory differs")
    estimated = sum((Decimal(str(r["estimated_cost_usd"])) for r in records), Decimal())
    estimated += Decimal(RECORDED_USED_COST_MICROUSD) / 1_000_000
    conservative = sum(
        (Decimal(str(r["estimated_cost_usd"])) if r["resource_auditable"] else CELL_RESERVATION
         for r in records), Decimal()
    ) + CELL_RESERVATION * 2
    if estimated != COMPLETED_ESTIMATED_COST or conservative != CONSERVATIVE_WITH_MISSING:
        raise ValueError("stopped cumulative cost differs")
    if conservative + CELL_RESERVATION * 4 > CAP:
        raise ValueError("remaining cells exceed original cumulative cap")
    url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not url:
        raise ValueError("dedicated database URL required")
    engine = create_async_engine(url)
    store = PostgresEventStore(engine)
    state = ComposeRangeRuntime(state_dir).state
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_cell92_stop_analysis",
    )
    try:
        await _missing_trace(
            store, STOP_RUN_ID, manifest["cells"][0], STOP_TRACE_SHA256,
            STOP_EVENT_IDENTITY_SHA256,
        )
        await _missing_trace(
            store, CELL4_RUN_ID, manifest["cells"][3], CELL4_TRACE_SHA256,
            CELL4_EVENT_IDENTITY_SHA256,
        )
        audits = {
            record["order"]: await _audit_cell(
                store, state, analysis, manifest["cells"][record["order"] - 1], record, None
            ) for record in records
        }
        replay_ids: set[UUID] = set()
        by_order = {r["order"]: r for r in records}
        for number in range(1, 46):
            receipt = json.loads((private / f"pair-postcheck-{number}.json").read_bytes())
            orders = (2 * number - 1, 2 * number)
            if (
                receipt.get("pair_number") != number
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("cell_ids") != [manifest["cells"][o - 1]["cell_id"] for o in orders]
            ):
                raise ValueError("retained pair receipt differs from authoritative replay")
            for offset, order in enumerate(orders):
                expected_audit = interrupted_pair_audit() if order == 86 else audits.get(order)
                expected_run = (str(CELL86_RUN_ID) if order == 86 else
                                by_order[order]["run_id"] if order in by_order else None)
                if expected_audit is not None and receipt["audits"][offset] != expected_audit:
                    raise ValueError("retained pair audit differs from PostgreSQL replay")
                if expected_run is not None and receipt["run_ids"][offset] != expected_run:
                    raise ValueError("retained pair run differs")
            replay_ids.update(
                UUID(run_id) for audit in receipt["audits"]
                for run_id in audit.get("validator_replay_run_ids", [])
            )
        cell86_trace = await store.read_run(CELL86_RUN_ID)
        if (
            len(cell86_trace) != 674
            or _trace_sha(cell86_trace) != CELL86_TRACE_SHA256
            or _identity_sha(cell86_trace) != CELL86_IDENTITY_SHA256
        ):
            raise ValueError("cell 86 interrupted trace changed")
        trace = await store.read_run(INTERRUPTED_RUN_ID)
        if (
            len(trace) != 61
            or [e.sequence_number for e in trace] != list(range(1, 62))
            or any(e.run_id != INTERRUPTED_RUN_ID for e in trace)
            or len({e.event_id for e in trace}) != 61
            or _trace_sha(trace) != INTERRUPTED_TRACE_SHA256
            or _identity_sha(trace) != INTERRUPTED_IDENTITY_SHA256
            or sum(isinstance(e, RunStarted) for e in trace) != 1
            or any(isinstance(e, (RunCompleted, ModelCallStarted)) for e in trace)
        ):
            raise ValueError("cell 92 interrupted source trace differs")
        async with engine.connect() as connection:
            actual_runs = set((await connection.execute(select(runs.c.run_id))).scalars().all())
            total_events = (
                await connection.execute(select(func.count()).select_from(events))
            ).scalar_one()
            active_models = (await connection.execute(
                select(model_reservations).where(model_reservations.c.settled_at.is_(None))
            )).mappings().all()
            active_actions = (await connection.execute(
                select(action_reservations).where(action_reservations.c.released_at.is_(None))
            )).mappings().all()
            reserved_usage = (await connection.execute(select(
                run_usage.c.run_id, run_usage.c.reserved_tokens,
                run_usage.c.reserved_cost_microusd,
            ).where((run_usage.c.reserved_tokens != 0) |
                    (run_usage.c.reserved_cost_microusd != 0)))).all()
            usage92 = (await connection.execute(
                select(run_usage).where(run_usage.c.run_id == INTERRUPTED_RUN_ID)
            )).mappings().one()
            usage86 = (await connection.execute(
                select(run_usage).where(run_usage.c.run_id == CELL86_RUN_ID)
            )).mappings().one()
        expected_runs = {
            STOP_RUN_ID, CELL4_RUN_ID, RETAINED_EARLY_SETUP_RUN_ID,
            CELL86_RUN_ID, CELL86_SETUP_RUN_ID, INTERRUPTED_RUN_ID,
            *(UUID(r[k]) for r in records for k in ("run_id", "setup_run_id")),
            *replay_ids,
        }
        if (
            actual_runs != expected_runs
            or len(actual_runs) != TOTAL_RUNS
            or total_events != TOTAL_EVENTS
            or len(active_models) != 1
            or active_models[0]["run_id"] != CELL86_RUN_ID
            or active_models[0]["call_id"] != UNSETTLED_CALL_ID
            or active_models[0]["reserved_tokens"] != UNSETTLED_RESERVED_TOKENS
            or active_models[0]["reserved_cost_microusd"] != UNSETTLED_RESERVED_COST_MICROUSD
            or reserved_usage != [(CELL86_RUN_ID, UNSETTLED_RESERVED_TOKENS,
                                   UNSETTLED_RESERVED_COST_MICROUSD)]
            or len(active_actions) != 1
            or active_actions[0]["run_id"] != INTERRUPTED_RUN_ID
            or active_actions[0]["action_id"] != ACTIVE_ACTION_ID
            or active_actions[0]["worker_id"] is not None
            or active_actions[0]["task_id"] is not None
            or usage92["used_model_calls"] != 0
            or usage92["used_cost_microusd"] != 0
            or usage92["reserved_tokens"] != 0
            or usage92["reserved_cost_microusd"] != 0
            or usage86["used_cost_microusd"] != RECORDED_USED_COST_MICROUSD
        ):
            raise ValueError("dedicated PostgreSQL stop inventory or reservation differs")
        return {
            "stopped_journal_sha256": STOP_JOURNAL_SHA256,
            "stopped_log_sha256": STOP_LOG_SHA256,
            "completed_source_cells": 88,
            "interrupted_cell_order": 92,
            "first_unstarted_order": 93,
            "retained_missing_orders": [1, 4, 86, 92],
            "retained_provider_failed_orders": [13, 14, 91],
            "completed_estimated_cost_usd": str(estimated),
            "conservative_cost_usd": str(conservative),
            "active_model_reservations": 1,
            "active_action_reservations": 1,
            "paid_calls_authorized": False,
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("execution-root", "source-root", "protocol-root", "manifest",
                 "approval", "journal", "state-dir"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(admit(
        args.execution_root, args.source_root, args.protocol_root, args.manifest,
        args.approval, args.journal, args.state_dir,
    )), sort_keys=True))


if __name__ == "__main__":
    main()
