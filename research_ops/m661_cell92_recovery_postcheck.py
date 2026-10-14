"""Read-only replay for a possible cells 93–96 continuation after cell 92.

The cell-86 model reservation and cell-92 action reservation remain active and
are charged conservatively. No reservation is released or settled here.
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
from m661_cell1_recovery_postcheck import _first_missing_row
from m661_cell4_recovery_postcheck import _fourth_missing_row, _missing_trace, _new_records
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
from m661_cell86_recovery_postcheck import _file_set_digest
from m661_cell86_recovery_postcheck import interrupted_pair_audit as cell86_pair_audit
from m661_cell92_interrupted_stop import (
    ACTIVE_ACTION_ID,
    INTERRUPTED_IDENTITY_SHA256,
    INTERRUPTED_RUN_ID,
    INTERRUPTED_TRACE_SHA256,
    PRIOR_PAIRS_DIGEST,
    PRIOR_PREFLIGHTS_DIGEST,
    STOP_JOURNAL_SHA256,
    STOP_LOG_SHA256,
    _stopped_third_records,
)
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import ModelCallCompleted, ModelCallStarted, RunCompleted, RunStarted
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import action_reservations, model_reservations, run_usage, runs


def _later_records(manifest: dict, path: Path) -> list[dict]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise ValueError("later journal is unsafe")
    lines = [json.loads(line) for line in path.read_bytes().splitlines()]
    if (
        not lines
        or lines[0].get("type") != "batch_started"
        or lines[0].get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or lines[0].get("expected_model_revision") != REVISION
        or lines[0].get("expected_upstream_provider") != UPSTREAM
        or Decimal(str(lines[0].get("max_estimated_usd"))) != CAP
        or (len(lines) - 1) % 2
    ):
        raise ValueError("later journal header or event pairing differs")
    records = []
    for order, (start, complete) in enumerate(zip(lines[1::2], lines[2::2], strict=True), 93):
        if order > 96:
            raise ValueError("later journal exceeds approved frozen assignments")
        cell = manifest["cells"][order - 1]
        if (
            start.get("type") != "cell_started"
            or complete.get("type") != "cell_completed"
            or start.get("order") != order
            or complete.get("order") != order
            or start.get("cell_id") != cell["cell_id"]
            or complete.get("cell_id") != cell["cell_id"]
            or start.get("run_id") != complete.get("run_id")
            or complete.get("experiment_sha256") != cell["experiment_sha256"]
        ):
            raise ValueError("later journal differs from frozen order")
        records.append(complete)
    if len({record["run_id"] for record in records}) != len(records):
        raise ValueError("later journal repeated a source run")
    return records


def interrupted_pair_audit() -> dict:
    return {
        "run_id": str(INTERRUPTED_RUN_ID),
        "trace_sha256": INTERRUPTED_TRACE_SHA256,
        "event_identity_sha256": INTERRUPTED_IDENTITY_SHA256,
        "status": "interrupted_action_outcome_unknown",
        "score_valid": False,
        "resource_auditable": False,
        "active_action_id": str(ACTIVE_ACTION_ID),
        "validator_replay_run_ids": [],
    }


async def postcheck(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
    *,
    require_complete: bool,
    allow_next_preflight: bool = False,
) -> dict:
    manifest, _ = _approval_and_manifest(
        execution_root, source_root, protocol_root, manifest_path, approval_path
    )
    private = approval_path.parent
    if journal_path != private / "journal.jsonl" or state_dir != private / "range-state":
        raise ValueError("private study paths differ")
    from m661_resume_after_cell92_permission import EXPECTED_CELL92_APPROVAL_SHA256, _scope_approval

    _scope_approval(private / "cell92-paid-continuation-approval.json", manifest)
    pinned = {
        "journal.jsonl": ORIGINAL_JOURNAL_SHA256,
        "continuation-journal.jsonl": CONTINUATION_JOURNAL_SHA256,
        "cell4-paid-continuation-journal.jsonl": CELL14_STOP_JOURNAL_SHA256,
        "collector-cell4-paid-session.log": CELL14_STOP_LOG_SHA256,
        "cell14-paid-continuation-journal.jsonl": CELL86_STOP_JOURNAL_SHA256,
        "collector-cell14-session.log": CELL86_STOP_LOG_SHA256,
        "cell86-paid-continuation-journal.jsonl": STOP_JOURNAL_SHA256,
        "collector-cell86-session.log": STOP_LOG_SHA256,
    }
    if (
        any(_sha256((private / name).read_bytes()) != digest for name, digest in pinned.items())
        or _file_set_digest([private / f"pair-postcheck-{i}.json" for i in range(1, 46)])
        != PRIOR_PAIRS_DIGEST
        or _file_set_digest([private / f"endpoint-preflight-{i}.json" for i in range(1, 47)])
        != PRIOR_PREFLIGHTS_DIGEST
    ):
        raise ValueError("stopped evidence or prior pair history changed")
    old, _ = _stopped_records(manifest, private)
    first = _new_records(manifest, private / "cell4-paid-continuation-journal.jsonl")
    second, _ = _completed_prefix(manifest, private)
    third, _ = _stopped_third_records(manifest, private)
    fourth_path = private / "cell92-paid-continuation-journal.jsonl"
    fourth = _later_records(manifest, fourth_path)
    if require_complete and len(fourth) != 4:
        raise ValueError("cells 93–96 are incomplete")
    if not fourth_path.exists():
        raise ValueError("approved continuation journal has not been created")
    recheck_raw = (private / "endpoint-recheck-cell-92.json").read_bytes()
    recheck = json.loads(recheck_raw)
    clearance = json.loads((private / "cell92-paid-clearance.json").read_bytes())
    if (
        clearance.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or clearance.get("stopped_journal_sha256") != STOP_JOURNAL_SHA256
        or clearance.get("pair46_receipt_sha256")
        != _sha256((private / "pair-postcheck-46.json").read_bytes())
        or clearance.get("continuation_approval_sha256") != EXPECTED_CELL92_APPROVAL_SHA256
        or clearance.get("endpoint_recheck_sha256") != _sha256(recheck_raw)
        or clearance.get("retained_missing_orders") != [1, 4, 86, 92]
        or clearance.get("retained_provider_failed_orders") != [13, 14, 91]
        or clearance.get("provider_health_epoch_start_order") != 93
        or clearance.get("next_cell_order") != 93
        or recheck.get("endpoint") != f"{UPSTREAM} | {REVISION}"
        or recheck.get("input_usd_per_million_tokens") != 3.0
        or recheck.get("output_usd_per_million_tokens") != 15.0
    ):
        raise ValueError("later clearance or endpoint differs")
    records = [*old, *first, *second, *third, *fourth]
    if len({record["run_id"] for record in records}) != len(records):
        raise ValueError("completed source run overlap")
    if len(list((private / "stages").glob("*.json"))) != len(records) or len(
        list((private / "traces").glob("*.json"))
    ) != len(records):
        raise ValueError("stage or trace artifact inventory differs")
    url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not url:
        raise ValueError("dedicated database URL required")
    engine = create_async_engine(url)
    store = PostgresEventStore(engine)
    state = ComposeRangeRuntime(state_dir).state
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_cell92_recovery_analysis",
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
        cell86_trace = await store.read_run(CELL86_RUN_ID)
        if (
            len(cell86_trace) != 674
            or _trace_sha(cell86_trace) != CELL86_TRACE_SHA256
            or _identity_sha(cell86_trace) != CELL86_IDENTITY_SHA256
        ):
            raise ValueError("cell-86 interrupted trace changed")
        trace = await store.read_run(INTERRUPTED_RUN_ID)
        if (
            len(trace) != 61
            or [e.sequence_number for e in trace] != list(range(1, 62))
            or _trace_sha(trace) != INTERRUPTED_TRACE_SHA256
            or _identity_sha(trace) != INTERRUPTED_IDENTITY_SHA256
            or sum(isinstance(e, RunStarted) for e in trace) != 1
            or any(isinstance(e, (RunCompleted, ModelCallStarted)) for e in trace)
        ):
            raise ValueError("interrupted cell-92 trace changed")
        replay_ids: set[UUID] = {
            UUID(run_id) for audit in audits.values()
            for run_id in audit.get("validator_replay_run_ids", [])
        }
        for number in range(1, 46):
            receipt = json.loads((private / f"pair-postcheck-{number}.json").read_bytes())
            for offset, order in enumerate((2 * number - 1, 2 * number)):
                expected = cell86_pair_audit() if order == 86 else audits.get(order)
                if expected is not None and receipt["audits"][offset] != expected:
                    raise ValueError("retained pair audit differs from PostgreSQL")
            replay_ids.update(
                UUID(run_id) for audit in receipt["audits"]
                for run_id in audit.get("validator_replay_run_ids", [])
            )
        by_order = {record["order"]: record for record in records}
        pair_count = 46 + len(fourth) // 2
        for number in range(46, pair_count + 1):
            receipt = json.loads((private / f"pair-postcheck-{number}.json").read_bytes())
            orders = (2 * number - 1, 2 * number)
            endpoint_raw = (private / f"endpoint-preflight-{number}.json").read_bytes()
            endpoint = json.loads(endpoint_raw)
            expected_audits = [
                interrupted_pair_audit() if order == 92 else audits[order] for order in orders
            ]
            expected_runs = [
                str(INTERRUPTED_RUN_ID) if order == 92 else by_order[order]["run_id"]
                for order in orders
            ]
            if (
                receipt.get("pair_number") != number
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("cell_ids")
                != [manifest["cells"][order - 1]["cell_id"] for order in orders]
                or receipt.get("run_ids") != expected_runs
                or receipt.get("audits") != expected_audits
                or receipt.get("endpoint_preflight_sha256") != _sha256(endpoint_raw)
                or endpoint.get("endpoint") != f"{UPSTREAM} | {REVISION}"
                or endpoint.get("input_usd_per_million_tokens") != 3.0
                or endpoint.get("output_usd_per_million_tokens") != 15.0
            ):
                raise ValueError("pair receipt or endpoint differs")
            through = [record for record in records if record["order"] <= orders[-1]]
            estimated = Decimal(RECORDED_USED_COST_MICROUSD) / 1_000_000 + sum(
                (Decimal(str(record["estimated_cost_usd"])) for record in through), Decimal()
            )
            conservative = CELL_RESERVATION * 2 + sum(
                (Decimal(str(record["estimated_cost_usd"]))
                 if record["resource_auditable"] else CELL_RESERVATION for record in through),
                Decimal(),
            )
            if (
                Decimal(str(receipt.get("cumulative_estimated_cost_usd"))) != estimated
                or Decimal(str(receipt.get("conservative_cost_usd"))) != conservative
            ):
                raise ValueError("pair cumulative cost differs")
            replay_ids.update(
                UUID(run_id) for audit in receipt["audits"]
                for run_id in audit.get("validator_replay_run_ids", [])
            )
        if len(list(private.glob("pair-postcheck-*.json"))) != pair_count:
            raise ValueError("pair receipt inventory differs")
        expected_preflights = 46 + (len(fourth) + 1) // 2
        allowed_preflights = (
            {expected_preflights, expected_preflights + 1}
            if allow_next_preflight else {expected_preflights}
        )
        if len(list(private.glob("endpoint-preflight-*.json"))) not in allowed_preflights:
            raise ValueError("endpoint preflight inventory differs")
        if len(list(private.glob("endpoint-recheck-cell-*.json"))) != 5:
            raise ValueError("endpoint recheck inventory differs")
        for replay_id in replay_ids:
            replay = await store.read_run(replay_id)
            if (
                not replay
                or [event.sequence_number for event in replay] != list(range(1, len(replay) + 1))
                or any(event.run_id != replay_id for event in replay)
                or len({event.event_id for event in replay}) != len(replay)
                or sum(isinstance(event, RunStarted) for event in replay) != 1
                or any(
                    isinstance(event, (ModelCallStarted, ModelCallCompleted))
                    for event in replay
                )
                or [event.status for event in replay if isinstance(event, RunCompleted)]
                != ["completed"]
            ):
                raise ValueError("validator replay contains model work or inconsistent events")
        async with engine.connect() as connection:
            actual_runs = set((await connection.execute(select(runs.c.run_id))).scalars().all())
            active_models = (await connection.execute(
                select(model_reservations).where(model_reservations.c.settled_at.is_(None))
            )).mappings().all()
            active_actions = (await connection.execute(
                select(action_reservations).where(action_reservations.c.released_at.is_(None))
            )).mappings().all()
            usage92 = (await connection.execute(
                select(run_usage).where(run_usage.c.run_id == INTERRUPTED_RUN_ID)
            )).mappings().one()
            active_usage = (await connection.execute(select(
                run_usage.c.run_id,
                run_usage.c.reserved_tokens,
                run_usage.c.reserved_cost_microusd,
            ).where((run_usage.c.reserved_tokens != 0) |
                    (run_usage.c.reserved_cost_microusd != 0)))).all()
        expected_runs = {
            STOP_RUN_ID, CELL4_RUN_ID, RETAINED_EARLY_SETUP_RUN_ID,
            CELL86_RUN_ID, CELL86_SETUP_RUN_ID, INTERRUPTED_RUN_ID,
            *(UUID(record[key]) for record in records for key in ("run_id", "setup_run_id")),
            *replay_ids,
        }
        if (
            actual_runs != expected_runs
            or len(active_models) != 1
            or active_models[0]["run_id"] != CELL86_RUN_ID
            or active_models[0]["call_id"] != UNSETTLED_CALL_ID
            or active_models[0]["worker_id"] is not None
            or active_models[0]["reserved_tokens"] != UNSETTLED_RESERVED_TOKENS
            or active_models[0]["reserved_cost_microusd"] != UNSETTLED_RESERVED_COST_MICROUSD
            or active_usage != [(CELL86_RUN_ID, UNSETTLED_RESERVED_TOKENS,
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
        ):
            raise ValueError("dedicated PostgreSQL work or reservations drifted")
        estimated = Decimal(RECORDED_USED_COST_MICROUSD) / 1_000_000 + sum(
            (Decimal(str(record["estimated_cost_usd"])) for record in records), Decimal()
        )
        conservative = CELL_RESERVATION * 2 + sum(
            (Decimal(str(record["estimated_cost_usd"]))
             if record["resource_auditable"] else CELL_RESERVATION for record in records),
            Decimal(),
        )
        if conservative + CELL_RESERVATION * (4 - len(fourth)) > CAP:
            raise ValueError("remaining frozen cells exceed original cumulative cap")
        rows = []
        for order, cell in enumerate(manifest["cells"], 1):
            if order == 1:
                first_audit = json.loads(
                    (private / "cell1-infrastructure-clearance.json").read_bytes()
                )["retained_first_cell"]
                rows.append(_first_missing_row(analysis, cell, first_audit))
            elif order == 4:
                rows.append(_fourth_missing_row(analysis, cell))
            elif order in (86, 92):
                run = CELL86_RUN_ID if order == 86 else INTERRUPTED_RUN_ID
                reason = (
                    "collector_interrupted_with_provider_outcome_unknown" if order == 86
                    else "collector_interrupted_during_action"
                )
                rows.append(analysis.missing_checkpoint_cell(
                    {**cell, "model_run_id": str(run)},
                    terminal_status="validation_failed", reason=reason,
                ))
            elif order in by_order:
                rows.append(json.loads(Path(by_order[order]["stage_path"]).read_bytes()))
            else:
                rows.append(analysis.missing_checkpoint_cell(
                    cell, terminal_status="unstarted", reason="no_model_run",
                ))
        curves = analysis.checkpoint_response_curves(
            rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
        )
        paired = analysis.paired_checkpoint_differences(
            rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
        )
        analysis_raw = (json.dumps(
            {"rows": rows, "curves": curves, "paired": paired}, sort_keys=True, indent=2
        ) + "\n").encode()
        return {
            "assigned_cells": 96,
            "completed_source_cells": len(records),
            "retained_missing_cells": [1, 4, 86, 92],
            "retained_provider_failed_cells": [13, 14, 91],
            "completed_pairs": pair_count,
            "completed_estimated_cost_usd": str(estimated),
            "conservative_cost_usd": str(conservative),
            "active_model_reservations": 1,
            "active_action_reservations": 1,
            "analysis_sha256": _sha256(analysis_raw),
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("execution-root", "source-root", "protocol-root", "manifest",
                 "approval", "journal", "state-dir"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--allow-prefix", action="store_true")
    parser.add_argument("--allow-next-preflight", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(postcheck(
        args.execution_root, args.source_root, args.protocol_root, args.manifest,
        args.approval, args.journal, args.state_dir,
        require_complete=not args.allow_prefix,
        allow_next_preflight=args.allow_next_preflight,
    ))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
