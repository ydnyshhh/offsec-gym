"""Read-only replay after the exact cell-86 interrupted provider call.

The one unresolved task-owned reservation remains visible and is charged the
frozen per-cell ceiling. This module never settles or retries it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m661_cell1_recovery import STOP_JOURNAL_SHA256 as ORIGINAL_JOURNAL_SHA256
from m661_cell1_recovery import STOP_RUN_ID
from m661_cell1_recovery_postcheck import _first_missing_row
from m661_cell4_recovery_postcheck import _fourth_missing_row, _new_records
from m661_cell4_stop import CELL4_RUN_ID, CONTINUATION_JOURNAL_SHA256, _stopped_records
from m661_cell14_provider_stop import STOP_JOURNAL_SHA256 as CELL14_STOP_JOURNAL_SHA256
from m661_cell14_provider_stop import STOP_LOG_SHA256 as CELL14_STOP_LOG_SHA256
from m661_cell86_interrupted_stop import (
    INTERRUPTED_IDENTITY_SHA256,
    INTERRUPTED_RUN_ID,
    INTERRUPTED_SETUP_RUN_ID,
    INTERRUPTED_TRACE_SHA256,
    RECORDED_USED_COST_MICROUSD,
    RETAINED_EARLY_SETUP_RUN_ID,
    SETTLED_CALL_ID,
    STOP_COMPLETED_CONSERVATIVE_COST,
    STOP_COMPLETED_ESTIMATED_COST,
    STOP_JOURNAL_SHA256,
    STOP_LOG_SHA256,
    UNSETTLED_CALL_ID,
    UNSETTLED_RESERVED_COST_MICROUSD,
    UNSETTLED_RESERVED_TOKENS,
    _completed_prefix,
)
from m661_feasibility_collect import (
    CAP,
    CELL_RESERVATION,
    EXPECTED_MANIFEST_SHA256,
    MODEL,
    REVISION,
    UPSTREAM,
    _approval_and_manifest,
    _audit_cell,
    _health_decision,
    _identity_sha,
    _load_module,
    _sha256,
    _trace_sha,
)
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
from offsecgym.storage.tables import action_reservations, model_reservations, run_usage, runs

PRIOR_PAIR_DIGEST = "8bbee39d9bc5f5a2af6998b5a2ab0c02ed559d33466a62cf2e3fa98bf6703323"
PRIOR_PREFLIGHT_DIGEST = "ee4cdab6d9f5ff4652a718e3b2ad29eedfe6db5469742341c09f95e668d843b7"


def _file_set_digest(paths: list[Path]) -> str:
    items = [[path.name, _sha256(path.read_bytes())] for path in paths]
    return hashlib.sha256(json.dumps(items, separators=(",", ":")).encode()).hexdigest()


def _third_records(manifest: dict, path: Path) -> list[dict]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise ValueError("later continuation journal is unsafe")
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
        raise ValueError("later continuation header or paired records differ")
    records = []
    for order, (started, completed) in enumerate(zip(lines[1::2], lines[2::2], strict=True), 87):
        if order > 96:
            raise ValueError("later continuation exceeds frozen assignments")
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
            raise ValueError("later continuation differs from frozen order")
        records.append(completed)
    if len({record["run_id"] for record in records}) != len(records):
        raise ValueError("later continuation repeated a run")
    return records


def interrupted_pair_audit() -> dict:
    return {
        "run_id": str(INTERRUPTED_RUN_ID),
        "trace_sha256": INTERRUPTED_TRACE_SHA256,
        "event_identity_sha256": INTERRUPTED_IDENTITY_SHA256,
        "status": "interrupted_provider_outcome_unknown",
        "score_valid": False,
        "resource_auditable": False,
        "unsettled_call_id": str(UNSETTLED_CALL_ID),
        "reserved_cost_microusd": UNSETTLED_RESERVED_COST_MICROUSD,
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
    stopped_hashes = {
        "journal.jsonl": ORIGINAL_JOURNAL_SHA256,
        "continuation-journal.jsonl": CONTINUATION_JOURNAL_SHA256,
        "cell4-paid-continuation-journal.jsonl": CELL14_STOP_JOURNAL_SHA256,
        "collector-cell4-paid-session.log": CELL14_STOP_LOG_SHA256,
        "cell14-paid-continuation-journal.jsonl": STOP_JOURNAL_SHA256,
        "collector-cell14-session.log": STOP_LOG_SHA256,
    }
    if any(
        _sha256((private / name).read_bytes()) != digest for name, digest in stopped_hashes.items()
    ):
        raise ValueError("prior stopped evidence bytes changed")
    old_records, _ = _stopped_records(manifest, private)
    first_records = _new_records(manifest, private / "cell4-paid-continuation-journal.jsonl")
    second_records, _ = _completed_prefix(manifest, private)
    third_path = private / "cell86-paid-continuation-journal.jsonl"
    third_records = _third_records(manifest, third_path)
    if third_path.exists():
        from m661_resume_after_cell86_permission import (
            EXPECTED_CELL86_APPROVAL_SHA256,
            _scope_approval,
        )

        _scope_approval(private / "cell86-paid-continuation-approval.json", manifest)
        recheck_raw = (private / "endpoint-recheck-cell-86.json").read_bytes()
        recheck = json.loads(recheck_raw)
        clearance = json.loads((private / "cell86-paid-clearance.json").read_bytes())
        if (
            clearance.get("protocol") != manifest["protocol"]
            or clearance.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
            or clearance.get("stopped_journal_sha256") != STOP_JOURNAL_SHA256
            or clearance.get("pair43_receipt_sha256")
            != _sha256((private / "pair-postcheck-43.json").read_bytes())
            or clearance.get("continuation_approval_sha256") != EXPECTED_CELL86_APPROVAL_SHA256
            or clearance.get("endpoint_recheck_sha256") != _sha256(recheck_raw)
            or clearance.get("retained_missing_orders") != [1, 4, 86]
            or clearance.get("retained_provider_failed_orders") != [13, 14]
            or clearance.get("provider_health_epoch_start_order") != 15
            or clearance.get("next_cell_order") != 87
            or recheck.get("endpoint") != f"{UPSTREAM} | {REVISION}"
            or recheck.get("input_usd_per_million_tokens") != 3.0
            or recheck.get("output_usd_per_million_tokens") != 15.0
        ):
            raise ValueError("cell-86 clearance or endpoint recheck differs")
    elif any(
        (private / name).exists()
        for name in ("cell86-paid-clearance.json", "endpoint-recheck-cell-86.json")
    ):
        raise ValueError("cell-86 clearance exists without new journal")
    if require_complete and len(third_records) != 10:
        raise ValueError("frozen remaining assignments are incomplete")
    records = [*old_records, *first_records, *second_records, *third_records]
    if len({record["run_id"] for record in records}) != len(records):
        raise ValueError("source run identity overlaps")
    if (
        _sha256((private / "cell14-paid-continuation-journal.jsonl").read_bytes())
        != STOP_JOURNAL_SHA256
    ):
        raise ValueError("interrupted journal bytes changed")
    if (
        _file_set_digest([private / f"pair-postcheck-{i}.json" for i in range(1, 43)])
        != PRIOR_PAIR_DIGEST
    ):
        raise ValueError("prior pair receipts changed")
    if (
        _file_set_digest([private / f"endpoint-preflight-{i}.json" for i in range(1, 44)])
        != PRIOR_PREFLIGHT_DIGEST
    ):
        raise ValueError("prior endpoint preflights changed")
    url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not url:
        raise ValueError("dedicated database URL required")
    engine = create_async_engine(url)
    store = PostgresEventStore(engine)
    state = ComposeRangeRuntime(state_dir).state
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_cell86_recovery_analysis",
    )
    try:
        audits = {
            record["order"]: await _audit_cell(
                store, state, analysis, manifest["cells"][record["order"] - 1], record, None
            )
            for record in records
        }
        trace = await store.read_run(INTERRUPTED_RUN_ID)
        setup_trace = await store.read_run(INTERRUPTED_SETUP_RUN_ID)
        starts = [event for event in trace if isinstance(event, ModelCallStarted)]
        completed_calls = [event for event in trace if isinstance(event, ModelCallCompleted)]
        if (
            len(trace) != 674
            or [event.sequence_number for event in trace] != list(range(1, 675))
            or any(event.run_id != INTERRUPTED_RUN_ID for event in trace)
            or len({event.event_id for event in trace}) != 674
            or _trace_sha(trace) != INTERRUPTED_TRACE_SHA256
            or _identity_sha(trace) != INTERRUPTED_IDENTITY_SHA256
            or len(starts) != 2
            or {event.call_id for event in starts} != {SETTLED_CALL_ID, UNSETTLED_CALL_ID}
            or len(completed_calls) != 1
            or completed_calls[0].call_id != SETTLED_CALL_ID
            or any(event.provider != "openrouter" or event.model != MODEL for event in starts)
            or len([event for event in trace if isinstance(event, RunStarted)]) != 1
            or any(isinstance(event, (ModelCallFailed, RunCompleted)) for event in trace)
            or len(setup_trace) != 34
            or [event.status for event in setup_trace if isinstance(event, RunCompleted)]
            != ["completed"]
            or any(isinstance(event, ModelCallStarted) for event in setup_trace)
        ):
            raise ValueError("retained interrupted source or setup trace differs")
        pair43 = private / "pair-postcheck-43.json"
        if third_path.exists() and not pair43.exists():
            raise ValueError("pair 43 must close before new model work")
        replay_ids: set[UUID] = set()
        for number in range(1, 43):
            receipt = json.loads((private / f"pair-postcheck-{number}.json").read_bytes())
            replay_ids.update(
                UUID(run_id)
                for audit in receipt["audits"]
                for run_id in audit.get("validator_replay_run_ids", [])
            )
        pair_count = 42 + int(pair43.exists()) + len(third_records) // 2
        record_by_order = {record["order"]: record for record in records}
        for number in range(43, pair_count + 1):
            receipt = json.loads((private / f"pair-postcheck-{number}.json").read_bytes())
            orders = (2 * number - 1, 2 * number)
            endpoint_raw = (private / f"endpoint-preflight-{number}.json").read_bytes()
            endpoint = json.loads(endpoint_raw)
            expected_audits = [
                interrupted_pair_audit() if order == 86 else audits[order] for order in orders
            ]
            expected_runs = [
                str(INTERRUPTED_RUN_ID) if order == 86 else record_by_order[order]["run_id"]
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
                raise ValueError("later pair receipt or endpoint differs")
            completed_through_pair = [record for record in records if record["order"] <= orders[-1]]
            pair_estimated = (
                STOP_COMPLETED_ESTIMATED_COST
                + Decimal("0.026463")
                + sum(
                    (
                        Decimal(str(record["estimated_cost_usd"]))
                        for record in completed_through_pair
                        if record["order"] >= 87
                    ),
                    Decimal(),
                )
            )
            pair_conservative = (
                STOP_COMPLETED_CONSERVATIVE_COST
                + CELL_RESERVATION
                + sum(
                    (
                        Decimal(str(record["estimated_cost_usd"]))
                        if record["resource_auditable"]
                        else CELL_RESERVATION
                        for record in completed_through_pair
                        if record["order"] >= 87
                    ),
                    Decimal(),
                )
            )
            if (
                Decimal(str(receipt.get("cumulative_estimated_cost_usd"))) != pair_estimated
                or Decimal(str(receipt.get("conservative_cost_usd"))) != pair_conservative
            ):
                raise ValueError("later pair cost differs")
            replay_ids.update(
                UUID(run_id)
                for audit in receipt["audits"]
                for run_id in audit.get("validator_replay_run_ids", [])
            )
        if len(list(private.glob("pair-postcheck-*.json"))) != pair_count:
            raise ValueError("pair receipt inventory differs")
        expected_preflights = 43 + (len(third_records) + 1) // 2
        actual_preflights = len(list(private.glob("endpoint-preflight-*.json")))
        allowed = (
            {expected_preflights, expected_preflights + 1}
            if allow_next_preflight
            else {expected_preflights}
        )
        if actual_preflights not in allowed:
            raise ValueError("endpoint preflight inventory differs")
        expected_rechecks = {
            "endpoint-recheck-cell-1.json",
            "endpoint-recheck-cell-4.json",
            "endpoint-recheck-cell-14.json",
        }
        if third_path.exists():
            expected_rechecks.add("endpoint-recheck-cell-86.json")
        if {
            path.name for path in private.glob("endpoint-recheck-cell-*.json")
        } != expected_rechecks:
            raise ValueError("endpoint recheck inventory differs")
        if len(list((private / "stages").glob("*.json"))) != len(records) or len(
            list((private / "traces").glob("*.json"))
        ) != len(records):
            raise ValueError("stage or source trace artifact inventory differs")
        async with engine.connect() as connection:
            actual_runs = set((await connection.execute(select(runs.c.run_id))).scalars().all())
            actions = (
                await connection.execute(
                    select(func.count())
                    .select_from(action_reservations)
                    .where(action_reservations.c.released_at.is_(None))
                )
            ).scalar_one()
            active_models = (
                (
                    await connection.execute(
                        select(model_reservations).where(model_reservations.c.settled_at.is_(None))
                    )
                )
                .mappings()
                .all()
            )
            active_usage = (
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
            interrupted_usage = (
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
            *(UUID(record[key]) for record in records for key in ("run_id", "setup_run_id")),
            *replay_ids,
        }
        for replay_id in replay_ids:
            replay = await store.read_run(replay_id)
            if (
                not replay
                or [event.sequence_number for event in replay] != list(range(1, len(replay) + 1))
                or any(event.run_id != replay_id for event in replay)
                or len({event.event_id for event in replay}) != len(replay)
                or sum(isinstance(event, RunStarted) for event in replay) != 1
                or any(
                    isinstance(event, (ModelCallStarted, ModelCallCompleted)) for event in replay
                )
                or [event.status for event in replay if isinstance(event, RunCompleted)]
                != ["completed"]
            ):
                raise ValueError("validator replay contains model work or inconsistent events")
        if (
            actual_runs != expected_runs
            or actions != 0
            or len(active_models) != 1
            or active_models[0]["run_id"] != INTERRUPTED_RUN_ID
            or active_models[0]["call_id"] != UNSETTLED_CALL_ID
            or active_models[0]["worker_id"] is not None
            or active_models[0]["reserved_tokens"] != UNSETTLED_RESERVED_TOKENS
            or active_models[0]["reserved_cost_microusd"] != UNSETTLED_RESERVED_COST_MICROUSD
            or active_usage
            != [(INTERRUPTED_RUN_ID, UNSETTLED_RESERVED_TOKENS, UNSETTLED_RESERVED_COST_MICROUSD)]
            or interrupted_usage["used_cost_microusd"] != RECORDED_USED_COST_MICROUSD
        ):
            raise ValueError("dedicated PostgreSQL has extra work or reservation drift")
        new_estimated = sum(
            (Decimal(str(record["estimated_cost_usd"])) for record in third_records), Decimal()
        )
        new_conservative = sum(
            (
                Decimal(str(record["estimated_cost_usd"]))
                if record["resource_auditable"]
                else CELL_RESERVATION
                for record in third_records
            ),
            Decimal(),
        )
        completed_estimated = STOP_COMPLETED_ESTIMATED_COST + Decimal("0.026463") + new_estimated
        conservative = STOP_COMPLETED_CONSERVATIVE_COST + CELL_RESERVATION + new_conservative
        remaining = 10 - len(third_records)
        if conservative + CELL_RESERVATION * remaining > CAP:
            raise ValueError("remaining frozen reservations exceed cumulative cap")
        epoch = second_records.copy()
        for record in third_records:
            if _health_decision(epoch) != "continue":
                raise ValueError("collector continued after provider-health stop")
            epoch.append(record)
        rows = []
        for order, cell in enumerate(manifest["cells"], 1):
            if order == 1:
                first_audit = json.loads(
                    (private / "cell1-infrastructure-clearance.json").read_bytes()
                )["retained_first_cell"]
                rows.append(_first_missing_row(analysis, cell, first_audit))
            elif order == 4:
                rows.append(_fourth_missing_row(analysis, cell))
            elif order == 86:
                rows.append(
                    analysis.missing_checkpoint_cell(
                        {**cell, "model_run_id": str(INTERRUPTED_RUN_ID)},
                        terminal_status="validation_failed",
                        reason="collector_interrupted_with_provider_outcome_unknown",
                    )
                )
            elif order in record_by_order:
                rows.append(json.loads(Path(record_by_order[order]["stage_path"]).read_bytes()))
            else:
                rows.append(
                    analysis.missing_checkpoint_cell(
                        cell, terminal_status="unstarted", reason="no_model_run"
                    )
                )
        curves = analysis.checkpoint_response_curves(
            rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
        )
        paired = analysis.paired_checkpoint_differences(
            rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
        )
        analysis_raw = (
            json.dumps({"rows": rows, "curves": curves, "paired": paired}, sort_keys=True, indent=2)
            + "\n"
        ).encode()
        return {
            "assigned_cells": 96,
            "completed_source_cells": len(records),
            "retained_infrastructure_missing_cells": [1, 4],
            "retained_provider_failed_cells": [13, 14],
            "retained_interrupted_cell": 86,
            "completed_pairs": pair_count,
            "completed_estimated_cost_usd": str(completed_estimated),
            "conservative_cost_with_interrupted_cell_usd": str(conservative),
            "active_model_reservations": 1,
            "analysis_sha256": _sha256(analysis_raw),
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
    parser.add_argument("--allow-prefix", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        postcheck(
            args.execution_root,
            args.source_root,
            args.protocol_root,
            args.manifest,
            args.approval,
            args.journal,
            args.state_dir,
            require_complete=not args.allow_prefix,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
