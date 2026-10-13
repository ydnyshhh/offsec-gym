"""Read-only PostgreSQL replay of M6.6.1 with cells 1 and 4 retained missing."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m661_cached_build_permissions import _new_approval
from m661_cell1_recovery import (
    STOP_EVENT_IDENTITY_SHA256,
    STOP_JOURNAL_SHA256,
    STOP_RUN_ID,
    STOP_TRACE_SHA256,
    _stopped_journal,
)
from m661_cell1_recovery_postcheck import _first_missing_row
from m661_cell4_repaired_stop import _repair_evidence, retained_four_pair_audit
from m661_cell4_stop import (
    CELL4_EVENT_IDENTITY_SHA256,
    CELL4_RUN_ID,
    CELL4_TRACE_SHA256,
    CONTINUATION_JOURNAL_SHA256,
    _stopped_records,
)
from m661_feasibility_collect import (
    CAP,
    EXPECTED_MANIFEST_SHA256,
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
from m661_resume_after_cell4_permission import (
    EXPECTED_PAID_APPROVAL_SHA256,
    _inventory_with_retained_missing,
    _scope_approval,
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
from offsecgym.storage.projection import project_controller_events
from offsecgym.storage.tables import action_reservations, model_reservations, run_usage


def _new_records(manifest: dict, path: Path, *, start_order: int = 5) -> list[dict]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("cell-four continuation journal is absent or unsafe")
    lines = [json.loads(line) for line in path.read_bytes().splitlines()]
    if not lines or any(
        lines[0].get(key) != value
        for key, value in (
            ("type", "batch_started"),
            ("manifest_sha256", EXPECTED_MANIFEST_SHA256),
            ("expected_model_revision", REVISION),
            ("expected_upstream_provider", UPSTREAM),
            ("max_estimated_usd", float(CAP)),
        )
    ):
        raise ValueError("cell-four continuation header differs")
    if (len(lines) - 1) % 2:
        raise ValueError("interrupted new cell requires separate reconciliation")
    records = []
    for offset, (started, record) in enumerate(zip(lines[1::2], lines[2::2], strict=True)):
        order = offset + start_order
        if order > 96:
            raise ValueError("new continuation exceeds frozen assignments")
        cell = manifest["cells"][order - 1]
        if any(
            (
                started.get("type") != "cell_started",
                record.get("type") != "cell_completed",
                started.get("order") != order,
                record.get("order") != order,
                started.get("cell_id") != cell["cell_id"],
                record.get("cell_id") != cell["cell_id"],
                started.get("run_id") != record.get("run_id"),
                record.get("experiment_sha256") != cell["experiment_sha256"],
            )
        ):
            raise ValueError("new continuation differs from frozen order")
        records.append(record)
    if len({item["run_id"] for item in records}) != len(records):
        raise ValueError("new continuation repeats a run")
    return records


def _health_stops_continuation(
    records: list[dict], record_index: int, *, second_journal: bool
) -> bool:
    epoch = (
        records[12 : record_index + 1]
        if second_journal and record_index >= 12
        else records[: record_index + 1]
    )
    decision = _health_decision(epoch)
    return decision != "continue" and not (
        second_journal and record_index == 11 and decision == "pause"
    )


async def _missing_trace(events, run_id: UUID, cell: dict, trace_sha: str, identity_sha: str):
    trace = await events.read_run(run_id)
    if (
        len(trace) != 2
        or [type(item) for item in trace] != [RunStarted, RunCompleted]
        or [item.sequence_number for item in trace] != [1, 2]
        or any(item.run_id != run_id for item in trace)
        or len({item.event_id for item in trace}) != 2
        or trace[0].experiment_hash != cell["experiment_sha256"]
        or trace[1].status != "environment_failed"
        or _trace_sha(trace) != trace_sha
        or _identity_sha(trace) != identity_sha
        or any(
            isinstance(item, (ModelCallStarted, ModelCallCompleted, ModelCallFailed))
            for item in trace
        )
    ):
        raise ValueError("retained infrastructure-missing trace differs")
    projection = project_controller_events(trace)
    if (
        projection.active_actions
        or projection.model_reservations
        or projection.active_workers
        or any(hold.active for hold in projection.admission_holds.values())
    ):
        raise ValueError("retained infrastructure run has an active reservation")


def _fourth_missing_row(analysis, cell: dict) -> dict:
    row = analysis.missing_checkpoint_cell(
        {**cell, "model_run_id": str(CELL4_RUN_ID)},
        terminal_status="environment_failed",
        reason="infrastructure_permission_failure_before_checkpoint",
    )
    row.update(
        {
            "resource_auditable": True,
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "http_actions": 0,
            "trusted_setup_http_actions": 0,
            "resource_recovered_from_authoritative_events": True,
        }
    )
    return row


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
    second_journal_path: Path | None = None,
) -> dict:
    manifest, _ = _approval_and_manifest(
        execution_root, source_root, protocol_root, manifest_path, approval_path
    )
    private = approval_path.parent
    if journal_path != private / "journal.jsonl" or state_dir != private / "range-state":
        raise ValueError("private study paths differ")
    _stopped_journal(manifest, journal_path)
    _new_approval(private / "cell4-continuation-approval.json", manifest)
    _scope_approval(private / "cell4-paid-continuation-approval.json", manifest)
    old_records, _ = _stopped_records(manifest, private)
    if (
        _sha256((private / "continuation-journal.jsonl").read_bytes())
        != CONTINUATION_JOURNAL_SHA256
    ):
        raise ValueError("old continuation journal changed")
    repair = _repair_evidence(private, state_dir)
    endpoint_recheck_path = private / "endpoint-recheck-cell-4.json"
    endpoint_recheck_raw = endpoint_recheck_path.read_bytes()
    endpoint_recheck = json.loads(endpoint_recheck_raw)
    clearance = json.loads((private / "cell4-paid-clearance.json").read_bytes())
    if (
        clearance.get("protocol") != manifest["protocol"]
        or clearance.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or clearance.get("original_journal_sha256") != STOP_JOURNAL_SHA256
        or clearance.get("stopped_continuation_journal_sha256") != CONTINUATION_JOURNAL_SHA256
        or clearance.get("retained_cell1_run_id") != str(STOP_RUN_ID)
        or clearance.get("retained_cell4_run_id") != str(CELL4_RUN_ID)
        or clearance.get("repair") != repair
        or clearance.get("continuation_approval_sha256") != EXPECTED_PAID_APPROVAL_SHA256
        or clearance.get("endpoint_recheck_sha256") != _sha256(endpoint_recheck_raw)
        or clearance.get("next_cell_order") != 5
        or endpoint_recheck.get("endpoint") != f"{UPSTREAM} | {REVISION}"
        or endpoint_recheck.get("input_usd_per_million_tokens") != 3.0
        or endpoint_recheck.get("output_usd_per_million_tokens") != 15.0
    ):
        raise ValueError("paid continuation clearance or selected endpoint differs")
    new_path = private / "cell4-paid-continuation-journal.jsonl"
    first_records = _new_records(manifest, new_path)
    second_records: list[dict] = []
    if second_journal_path is not None:
        from m661_cell14_provider_stop import STOP_JOURNAL_SHA256 as CELL14_STOP_JOURNAL_SHA256
        from m661_resume_after_cell14_pause import (
            EXPECTED_CELL14_APPROVAL_SHA256,
        )
        from m661_resume_after_cell14_pause import (
            _scope_approval as _cell14_scope_approval,
        )

        if second_journal_path != private / "cell14-paid-continuation-journal.jsonl":
            raise ValueError("cell-14 journal path differs")
        if _sha256(new_path.read_bytes()) != CELL14_STOP_JOURNAL_SHA256 or len(first_records) != 10:
            raise ValueError("retained cell-14 stop journal differs")
        _cell14_scope_approval(private / "cell14-paid-continuation-approval.json", manifest)
        second_records = _new_records(manifest, second_journal_path, start_order=15)
        if {item["run_id"] for item in first_records} & {item["run_id"] for item in second_records}:
            raise ValueError("provider-pause continuation reused a source run")
        recheck_raw = (private / "endpoint-recheck-cell-14.json").read_bytes()
        recheck = json.loads(recheck_raw)
        clearance = json.loads((private / "cell14-provider-clearance.json").read_bytes())
        if (
            clearance.get("protocol") != manifest["protocol"]
            or clearance.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
            or clearance.get("stopped_journal_sha256") != CELL14_STOP_JOURNAL_SHA256
            or clearance.get("pair7_receipt_sha256")
            != _sha256((private / "pair-postcheck-7.json").read_bytes())
            or clearance.get("continuation_approval_sha256") != EXPECTED_CELL14_APPROVAL_SHA256
            or clearance.get("endpoint_recheck_sha256") != _sha256(recheck_raw)
            or clearance.get("retained_missing_orders") != [1, 4]
            or clearance.get("retained_provider_failed_orders") != [13, 14]
            or clearance.get("new_provider_health_epoch_start_order") != 15
            or clearance.get("next_cell_order") != 15
            or recheck.get("endpoint") != f"{UPSTREAM} | {REVISION}"
            or recheck.get("input_usd_per_million_tokens") != 3.0
            or recheck.get("output_usd_per_million_tokens") != 15.0
        ):
            raise ValueError("cell-14 provider clearance or endpoint differs")
    new_records = [*first_records, *second_records]
    if require_complete and len(new_records) != 92:
        raise ValueError("approved remaining assignments are incomplete")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("dedicated database URL required")
    engine = create_async_engine(database_url)
    events = PostgresEventStore(engine)
    state = ComposeRangeRuntime(state_dir).state
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_cell4_recovery_analysis",
    )
    records = [*old_records, *new_records]
    try:
        await _missing_trace(
            events,
            STOP_RUN_ID,
            manifest["cells"][0],
            STOP_TRACE_SHA256,
            STOP_EVENT_IDENTITY_SHA256,
        )
        await _missing_trace(
            events,
            CELL4_RUN_ID,
            manifest["cells"][3],
            CELL4_TRACE_SHA256,
            CELL4_EVENT_IDENTITY_SHA256,
        )
        audits = []
        rows = []
        cumulative = Decimal(0)
        conservative = Decimal(0)
        for index, cell in enumerate(manifest["cells"]):
            order = index + 1
            if order == 1:
                first_audit = json.loads(
                    (private / "cell1-infrastructure-clearance.json").read_bytes()
                )["retained_first_cell"]
                if (
                    first_audit.get("run_id") != str(STOP_RUN_ID)
                    or first_audit.get("trace_sha256") != STOP_TRACE_SHA256
                    or first_audit.get("event_identity_sha256") != STOP_EVENT_IDENTITY_SHA256
                ):
                    raise ValueError("retained first-cell audit differs")
                rows.append(_first_missing_row(analysis, cell, first_audit))
                continue
            if order == 4:
                rows.append(_fourth_missing_row(analysis, cell))
                continue
            record_index = order - 2 if order < 4 else order - 3
            if record_index >= len(records):
                rows.append(
                    analysis.missing_checkpoint_cell(
                        cell, terminal_status="unstarted", reason="no_model_run"
                    )
                )
                continue
            record = records[record_index]
            audit = await _audit_cell(events, state, analysis, cell, record, None)
            audits.append(audit)
            rows.append(json.loads(Path(record["stage_path"]).read_bytes()))
            cost = Decimal(str(record["estimated_cost_usd"]))
            cumulative += cost
            conservative += cost if record["resource_auditable"] else Decimal("1.80")
            if cumulative > CAP or conservative > CAP:
                raise ValueError("cumulative approved cost cap exceeded")
            if record_index + 1 < len(records) and _health_stops_continuation(
                records, record_index, second_journal=second_journal_path is not None
            ):
                raise ValueError("collector continued after provider-health stop")
        await _inventory_with_retained_missing(engine, events, records, audits)
        async with engine.connect() as connection:
            active_actions = (
                await connection.execute(
                    select(func.count())
                    .select_from(action_reservations)
                    .where(action_reservations.c.released_at.is_(None))
                )
            ).scalar_one()
            active_models = (
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
        if active_actions or active_models or any(tokens or cost for tokens, cost in usage):
            raise ValueError("dedicated PostgreSQL retains an active reservation")
        pair_count = 2 + len(new_records) // 2
        for number in range(1, pair_count + 1):
            pair = manifest["cells"][2 * number - 2 : 2 * number]
            receipt = json.loads((private / f"pair-postcheck-{number}.json").read_bytes())
            endpoint_path = (
                private / "endpoint-recheck-cell-1.json"
                if number == 1
                else private / f"endpoint-preflight-{number}.json"
            )
            endpoint_raw = endpoint_path.read_bytes()
            endpoint = json.loads(endpoint_raw)
            expected_runs = (
                [str(STOP_RUN_ID), old_records[0]["run_id"]]
                if number == 1
                else [old_records[1]["run_id"], str(CELL4_RUN_ID)]
                if number == 2
                else [item["run_id"] for item in records[2 * number - 4 : 2 * number - 2]]
            )
            if (
                receipt.get("protocol") != manifest["protocol"]
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("pair_number") != number
                or receipt.get("cell_ids") != [item["cell_id"] for item in pair]
                or receipt.get("run_ids") != expected_runs
                or receipt.get("endpoint_preflight_sha256") != _sha256(endpoint_raw)
                or endpoint.get("endpoint") != f"{UPSTREAM} | {REVISION}"
                or endpoint.get("input_usd_per_million_tokens") != 3.0
                or endpoint.get("output_usd_per_million_tokens") != 15.0
                or len(receipt.get("audits", [])) != 2
            ):
                raise ValueError("pair or selected endpoint receipt differs")
            expected_audits = (
                [first_audit, audits[0]]
                if number == 1
                else [audits[1], retained_four_pair_audit(pair[1])]
                if number == 2
                else audits[2 * number - 4 : 2 * number - 2]
            )
            if receipt["audits"] != expected_audits:
                raise ValueError("pair audits differ from replay")
            completed_in_pair = min(1 if number == 1 else 2 * number - 2, len(records))
            pair_cost = sum(
                (Decimal(str(item["estimated_cost_usd"])) for item in records[:completed_in_pair]),
                Decimal(0),
            )
            pair_conservative = sum(
                (
                    Decimal(str(item["estimated_cost_usd"]))
                    if item["resource_auditable"]
                    else Decimal("1.80")
                    for item in records[:completed_in_pair]
                ),
                Decimal(0),
            )
            if (
                Decimal(receipt["cumulative_estimated_cost_usd"]) != pair_cost
                or Decimal(receipt["conservative_cost_usd"]) != pair_conservative
            ):
                raise ValueError("pair cost replay differs")
        if len(list(private.glob("pair-postcheck-*.json"))) != pair_count:
            raise ValueError("pair receipt inventory differs")
        if len(list(private.glob("endpoint-preflight-*.json"))) != (
            pair_count + len(new_records) % 2
        ):
            raise ValueError("endpoint preflight inventory differs")
        expected_rechecks = {
            "endpoint-recheck-cell-1.json",
            "endpoint-recheck-cell-4.json",
        }
        if second_journal_path is not None:
            expected_rechecks.add("endpoint-recheck-cell-14.json")
        if {
            item.name for item in private.glob("endpoint-recheck-cell-*.json")
        } != expected_rechecks:
            raise ValueError("endpoint recheck inventory differs")
        if len(list((private / "stages").glob("*.json"))) != len(records):
            raise ValueError("stage artifact inventory differs")
        if len(list((private / "traces").glob("*.json"))) != len(records):
            raise ValueError("source trace artifact inventory differs")
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
            "retained_infrastructure_missing_cells": [1, 4],
            "completed_source_cells": len(records),
            "completed_pairs": pair_count,
            "original_journal_sha256": STOP_JOURNAL_SHA256,
            "stopped_continuation_journal_sha256": CONTINUATION_JOURNAL_SHA256,
            "new_continuation_journal_sha256": _sha256(new_path.read_bytes()),
            "cell14_continuation_journal_sha256": (
                _sha256(second_journal_path.read_bytes())
                if second_journal_path is not None
                else None
            ),
            "repair": repair,
            "estimated_cost_usd": str(cumulative),
            "conservative_cost_usd": str(conservative),
            "score_valid_cells": sum(record["score_valid"] for record in records),
            "provider_failed_cells": sum(
                record["status"] == "provider_failed" for record in records
            ),
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
    parser.add_argument("--cell14-continuation-journal", type=Path)
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
            second_journal_path=args.cell14_continuation_journal,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
