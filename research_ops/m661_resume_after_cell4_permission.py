"""Resume only frozen M6.6.1 cells 5–96 after exact cell-4 repair.

The original cell and journal are retained.  This file is fail closed until a
new human approval receipt is pinned in a separate provenance commit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from m661_cell1_recovery import STOP_JOURNAL_SHA256, STOP_RUN_ID
from m661_cell4_repaired_stop import admit, retained_four_pair_audit
from m661_cell4_stop import (
    CELL4_RUN_ID,
    CONTINUATION_JOURNAL_SHA256,
    CONTINUATION_LOG_SHA256,
    _stopped_records,
)
from m661_feasibility_collect import (
    CAP,
    CELL_RESERVATION,
    EXPECTED_MANIFEST_SHA256,
    PROTOCOL_COMMIT,
    REVISION,
    SOURCE_COMMIT,
    UPSTREAM,
    EndpointGuard,
    _approval_and_manifest,
    _audit_cell,
    _endpoint,
    _health_decision,
    _load_module,
    _record,
    _sha256,
    _spec,
    _utc_now,
    _write_bytes,
    _write_json,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.critical_checkpoint import CriticalCheckpointExperimentRunner
from offsecgym.research.m64_execute import Journal
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.events import ModelCallCompleted, ModelCallStarted, RunCompleted, RunStarted
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import action_reservations, model_reservations, run_usage, runs

CONTINUATION_SCOPE = "m661_feasibility_cells_5_through_96_once_after_cell4_mode_repair"
EXPECTED_PAID_APPROVAL_SHA256 = "6502419b322454debc4f4552ff0de6d8865586319b9a11c7dc3bf801b148aa0b"
OPERATIONAL_MERGE_COMMIT = "41b77717e6f059821fb9149f90c886afed87b304"
REPAIR_COMPLETION_SHA256 = "2e353af79f9e3ccdfd9e04dbfea169a9584635fb62afc230146150afa8e94347"


def _scope_approval(path: Path, manifest: dict) -> dict:
    if EXPECTED_PAID_APPROVAL_SHA256 == "0" * 64:
        raise ValueError("cell-4 paid continuation remains closed pending separate human approval")
    if path.name != "cell4-paid-continuation-approval.json" or path.is_symlink():
        raise ValueError("continuation approval path differs")
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("continuation approval is absent or not private")
    raw = path.read_bytes()
    if _sha256(raw) != EXPECTED_PAID_APPROVAL_SHA256:
        raise ValueError("continuation approval bytes differ")
    approval = json.loads(raw)
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    if (
        approved_at.utcoffset() is None
        or approved_at <= datetime(2026, 10, 12, 13, 41, tzinfo=UTC)
        or approval.get("approved_by") != "human_user"
        or approval.get("approved_for_paid_calls") is not True
        or approval.get("approval_scope") != CONTINUATION_SCOPE
        or approval.get("protocol") != manifest["protocol"]
        or approval.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("source_commit") != SOURCE_COMMIT
        or approval.get("protocol_commit") != PROTOCOL_COMMIT
        or approval.get("operational_merge_commit") != OPERATIONAL_MERGE_COMMIT
        or approval.get("repair_completion_sha256") != REPAIR_COMPLETION_SHA256
        or approval.get("assignment_sha256") != manifest["schedule"]["cell_assignment_sha256"]
        or approval.get("original_journal_sha256") != STOP_JOURNAL_SHA256
        or approval.get("stopped_continuation_journal_sha256") != CONTINUATION_JOURNAL_SHA256
        or approval.get("stopped_continuation_log_sha256") != CONTINUATION_LOG_SHA256
        or approval.get("retained_cell1_run_id") != str(STOP_RUN_ID)
        or approval.get("retained_cell4_run_id") != str(CELL4_RUN_ID)
        or approval.get("first_paid_cell_order") != 5
        or approval.get("last_paid_cell_order") != 96
        or approval.get("paid_cells_authorized") != 92
        or approval.get("retained_missing_orders") != [1, 4]
        or approval.get("no_retry_or_replacement") is not True
        or Decimal(str(approval.get("cumulative_cost_ceiling_usd"))) != CAP
    ):
        raise ValueError("continuation approval does not bind exact stopped scope")
    return approval


def _no_collector_running() -> None:
    output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    own_pid = os.getpid()
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or int(fields[0]) == own_pid:
            continue
        command = fields[1]
        executable = command.split(None, 1)[0].rsplit("/", 1)[-1]
        if "python" not in executable:
            continue
        if (
            "m661_feasibility_collect.py" in command
            or "m661_resume_after_cell1_permission.py" in command
            or "m661_resume_after_cell4_permission.py" in command
        ):
            raise ValueError("another M6.6.1 collector is running")


async def _inventory_with_retained_missing(engine, events, records: list[dict], audits: list[dict]):
    source_ids = {STOP_RUN_ID, CELL4_RUN_ID, *(UUID(record["run_id"]) for record in records)}
    setup_ids = {UUID(record["setup_run_id"]) for record in records}
    replay_ids = {UUID(value) for audit in audits for value in audit["validator_replay_run_ids"]}
    if (
        len(source_ids) != len(records) + 2
        or len(setup_ids) != len(records)
        or len(replay_ids) != sum(len(audit["validator_replay_run_ids"]) for audit in audits)
        or source_ids & setup_ids
        or source_ids & replay_ids
        or setup_ids & replay_ids
    ):
        raise ValueError("source, setup, or validator replay identity overlaps")
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
        raise ValueError("dedicated PostgreSQL has extra work or an active reservation")
    for replay_id in replay_ids:
        trace = await events.read_run(replay_id)
        if (
            not trace
            or [event.sequence_number for event in trace] != list(range(1, len(trace) + 1))
            or any(event.run_id != replay_id for event in trace)
            or len({event.event_id for event in trace}) != len(trace)
            or sum(isinstance(event, RunStarted) for event in trace) != 1
            or any(isinstance(event, (ModelCallStarted, ModelCallCompleted)) for event in trace)
            or [event.status for event in trace if isinstance(event, RunCompleted)] != ["completed"]
        ):
            raise ValueError("validator replay has model work or inconsistent events")


async def continue_cells(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    continuation_approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, _ = _approval_and_manifest(
        execution_root, source_root, protocol_root, manifest_path, approval_path
    )
    if continuation_approval_path.parent != approval_path.parent:
        raise ValueError("continuation approval directory differs")
    _scope_approval(continuation_approval_path, manifest)
    _no_collector_running()
    private_dir = journal_path.parent
    continuation_path = private_dir / "cell4-paid-continuation-journal.jsonl"
    clearance_path = private_dir / "cell4-paid-clearance.json"
    if (
        continuation_path.exists()
        or clearance_path.exists()
        or (private_dir / "endpoint-recheck-cell-4.json").exists()
    ):
        raise ValueError("continuation evidence already exists; do not restart")
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("private model key and database URL are required")
    stopped = await admit(
        execution_root,
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
    )
    # Pair two was interrupted after the assigned cell-four environment
    # failure. Close it from retained evidence; cell four remains missing.
    pair_two = manifest["cells"][2:4]
    old_records, _ = _stopped_records(manifest, private_dir)
    preflight_two = private_dir / "endpoint-preflight-2.json"
    preflight_two_raw = preflight_two.read_bytes()
    selected_two = json.loads(preflight_two_raw)
    if (
        selected_two.get("endpoint") != f"{UPSTREAM} | {REVISION}"
        or selected_two.get("input_usd_per_million_tokens") != 3.0
        or selected_two.get("output_usd_per_million_tokens") != 15.0
    ):
        raise ValueError("retained pair-two endpoint differs")
    missing_four = retained_four_pair_audit(pair_two[1])
    _write_json(
        private_dir / "pair-postcheck-2.json",
        {
            "protocol": manifest["protocol"],
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "pair_number": 2,
            "cell_ids": [cell["cell_id"] for cell in pair_two],
            "run_ids": [old_records[1]["run_id"], str(CELL4_RUN_ID)],
            "audits": [stopped["completed_audits"][1], missing_four],
            "endpoint_preflight_sha256": _sha256(preflight_two_raw),
            "original_pair1_preflight_sha256": None,
            "cumulative_estimated_cost_usd": stopped["cumulative_estimated_cost_usd"],
            "conservative_cost_usd": stopped["cumulative_estimated_cost_usd"],
        },
    )
    # Read-only permission qualification established that 022 makes freshly
    # compiled scripts readable to the image's non-root user.  Original build
    # artifacts remain untouched; private evidence writers use explicit 0600.
    os.umask(0o022)
    endpoint = _endpoint(manifest)
    recheck_sha = _write_json(private_dir / "endpoint-recheck-cell-4.json", endpoint)
    _write_json(
        clearance_path,
        {
            "protocol": manifest["protocol"],
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "original_journal_sha256": STOP_JOURNAL_SHA256,
            "stopped_continuation_journal_sha256": CONTINUATION_JOURNAL_SHA256,
            "retained_cell1_run_id": str(STOP_RUN_ID),
            "retained_cell4_run_id": str(CELL4_RUN_ID),
            "repair": stopped["repair"],
            "continuation_approval_sha256": EXPECTED_PAID_APPROVAL_SHA256,
            "endpoint_recheck_sha256": recheck_sha,
            "next_cell_order": 5,
        },
    )
    _write_bytes(continuation_path, b"")
    journal = Journal(
        continuation_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=(REVISION, UPSTREAM),
        max_estimated_usd=float(CAP),
    )
    # Fail before the first new paid call if retrospective pair closure or the
    # retained two-cell missingness cannot be replayed against PostgreSQL.
    from m661_cell4_recovery_postcheck import postcheck

    await postcheck(
        execution_root,
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
        require_complete=False,
    )
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    runtime = ComposeRangeRuntime(state_dir)
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_recovery_pinned_analysis",
    )
    guard = EndpointGuard()
    records = old_records
    audits: list[dict] = list(stopped["completed_audits"])
    committed_cost = Decimal(stopped["cumulative_estimated_cost_usd"])
    conservative_cost = committed_cost
    try:
        for pair_index in range(2, 48):
            pair = manifest["cells"][2 * pair_index : 2 * pair_index + 2]
            if (
                pair[0]["seed"] != pair[1]["seed"]
                or pair[0]["checkpoint_id"] != pair[1]["checkpoint_id"]
                or {pair[0]["variant"], pair[1]["variant"]} != {"vulnerable", "patched"}
            ):
                raise ValueError("frozen pair structure differs")
            endpoint_sha = _write_json(
                private_dir / f"endpoint-preflight-{pair_index + 1}.json", _endpoint(manifest)
            )
            pair_audits = []
            for cell in pair:
                if _sha256(journal_path.read_bytes()) != STOP_JOURNAL_SHA256:
                    raise ValueError("original stopped journal changed")
                stopped_raw = (private_dir / "continuation-journal.jsonl").read_bytes()
                if _sha256(stopped_raw) != CONTINUATION_JOURNAL_SHA256:
                    raise ValueError("stopped continuation journal changed")
                if conservative_cost + CELL_RESERVATION > CAP:
                    raise ValueError("next cell exceeds cumulative approved reservation")
                spec = _spec(source_root, manifest, cell)
                run_id = uuid4()
                journal.append(
                    {
                        "type": "cell_started",
                        "cell_id": cell["cell_id"],
                        "order": cell["order"],
                        "run_id": str(run_id),
                        "at": _utc_now(),
                    }
                )
                outcome = await CriticalCheckpointExperimentRunner(
                    runtime,
                    events,
                    guard.provider(os.environ["OPENROUTER_API_KEY"]),
                    cell["checkpoint_id"],
                ).run(spec, run_id=run_id)
                if outcome.build_id is None:
                    raise ValueError("completed cell lacks a bound build")
                build = runtime.state.verify_build_integrity(outcome.build_id)
                trace = await events.read_run(run_id)
                stage_path = private_dir / "stages" / f"{cell['cell_id']}.json"
                record = _record(cell, outcome, trace, build, state_dir, stage_path)
                oracle = StateOracleStore(runtime.state)._load_ground_truth(build.build_id)
                fixture = json.loads(
                    (runtime.state.build_dir(build.build_id) / "fixture.json").read_bytes()
                )
                row = await analysis.extract_checkpoint_assignment(
                    {**cell, "model_run_id": str(run_id)}, state_dir, events, oracle, fixture
                )
                if not row["trace_auditable"]:
                    raise ValueError("cell trace is unauditable; stop before next cell")
                record["stage_sha256"] = _sha256(
                    (json.dumps(row, sort_keys=True, indent=2) + "\n").encode()
                )
                trace_path = private_dir / "traces" / f"{cell['cell_id']}.json"
                record["trace_path"] = str(trace_path)
                trace_raw = (
                    json.dumps([item.model_dump(mode="json") for item in trace], sort_keys=True)
                    + "\n"
                ).encode()
                if _write_bytes(trace_path, trace_raw) != record["trace_sha256"]:
                    raise ValueError("source trace serialization differs")
                audited = await _audit_cell(
                    events, runtime.state, analysis, cell, record, stage_path
                )
                journal.append(record)
                journal.completed[cell["cell_id"]] = record
                records.append(record)
                audits.append(audited)
                pair_audits.append(audited)
                committed_cost += Decimal(record["estimated_cost_usd"])
                conservative_cost += (
                    Decimal(record["estimated_cost_usd"])
                    if record["resource_auditable"]
                    else CELL_RESERVATION
                )
                await _inventory_with_retained_missing(engine, events, records, audits)
                print(
                    json.dumps(
                        {
                            "cell": cell["order"],
                            "status": record["status"],
                            "score_valid": record["score_valid"],
                            "estimated_cost_usd": str(committed_cost),
                            "conservative_cost_usd": str(conservative_cost),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if guard.stopped or record["status"] not in {
                    "completed",
                    "budget_exhausted",
                    "agent_failed",
                    "provider_failed",
                }:
                    raise ValueError("provider or run status requires study stop")
                if _health_decision(records) != "continue":
                    raise ValueError("frozen provider-health gate requires study stop")
            _write_json(
                private_dir / f"pair-postcheck-{pair_index + 1}.json",
                {
                    "protocol": manifest["protocol"],
                    "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                    "pair_number": pair_index + 1,
                    "cell_ids": [cell["cell_id"] for cell in pair],
                    "run_ids": [item["run_id"] for item in records[-2:]],
                    "audits": pair_audits,
                    "endpoint_preflight_sha256": endpoint_sha,
                    "original_pair1_preflight_sha256": None,
                    "cumulative_estimated_cost_usd": str(committed_cost),
                    "conservative_cost_usd": str(conservative_cost),
                },
            )
        return {
            "completed_assigned_cells": 96,
            "newly_executed_cells": len(records) - 2,
            "estimated_cost_usd": str(committed_cost),
            "conservative_cost_usd": str(conservative_cost),
        }
    finally:
        journal.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "execution-root",
        "source-root",
        "protocol-root",
        "manifest",
        "approval",
        "continuation-approval",
        "journal",
        "state-dir",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(
        continue_cells(
            args.execution_root,
            args.source_root,
            args.protocol_root,
            args.manifest,
            args.approval,
            args.continuation_approval,
            args.journal,
            args.state_dir,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
