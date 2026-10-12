"""Resume only frozen M6.6.1 cells 2–96 after exact cell-1 reconciliation.

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

from m661_cell1_recovery import (
    EXPECTED_CONTINUATION_APPROVAL_SHA256,
    STOP_JOURNAL_SHA256,
    STOP_RUN_ID,
    reconcile,
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.critical_checkpoint import CriticalCheckpointExperimentRunner
from offsecgym.research.m64_execute import Journal
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.events import ModelCallCompleted, ModelCallStarted, RunCompleted, RunStarted
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import runs

CONTINUATION_SCOPE = "m661_feasibility_cells_2_through_96_once_after_cell1_environment_failure"


def _scope_approval(path: Path, manifest: dict) -> dict:
    if EXPECTED_CONTINUATION_APPROVAL_SHA256 == "0" * 64:
        raise ValueError("cell-1 continuation remains closed pending separate human approval")
    if path.name != "cell1-continuation-approval.json" or path.is_symlink():
        raise ValueError("continuation approval path differs")
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("continuation approval is absent or not private")
    raw = path.read_bytes()
    if _sha256(raw) != EXPECTED_CONTINUATION_APPROVAL_SHA256:
        raise ValueError("continuation approval bytes differ")
    approval = json.loads(raw)
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    if (
        approved_at.utcoffset() is None
        or approved_at <= datetime(2026, 10, 12, 6, 58, 53, tzinfo=UTC)
        or approval.get("approved_by") != "human_user"
        or approval.get("approved_for_paid_calls") is not True
        or approval.get("approval_scope") != CONTINUATION_SCOPE
        or approval.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("source_commit") != SOURCE_COMMIT
        or approval.get("protocol_commit") != PROTOCOL_COMMIT
        or approval.get("assignment_sha256") != manifest["schedule"]["cell_assignment_sha256"]
        or approval.get("stopped_journal_sha256") != STOP_JOURNAL_SHA256
        or approval.get("retained_cell1_run_id") != str(STOP_RUN_ID)
        or approval.get("first_paid_cell_order") != 2
        or approval.get("last_paid_cell_order") != 96
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
        ):
            raise ValueError("another M6.6.1 collector is running")


async def _inventory_with_retained_first(engine, events, records: list[dict], audits: list[dict]):
    source_ids = {STOP_RUN_ID, *(UUID(record["run_id"]) for record in records)}
    setup_ids = {UUID(record["setup_run_id"]) for record in records}
    replay_ids = {UUID(value) for audit in audits for value in audit["validator_replay_run_ids"]}
    if (
        len(source_ids) != len(records) + 1
        or len(setup_ids) != len(records)
        or source_ids & setup_ids
        or source_ids & replay_ids
        or setup_ids & replay_ids
    ):
        raise ValueError("source, setup, or validator replay identity overlaps")
    async with engine.connect() as connection:
        actual = set((await connection.execute(select(runs.c.run_id))).scalars().all())
    if actual != source_ids | setup_ids | replay_ids:
        raise ValueError("dedicated PostgreSQL has an extra or missing run")
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
    continuation_path = private_dir / "continuation-journal.jsonl"
    clearance_path = private_dir / "cell1-infrastructure-clearance.json"
    if (
        continuation_path.exists()
        or clearance_path.exists()
        or (private_dir / "endpoint-recheck-cell-1.json").exists()
    ):
        raise ValueError("continuation evidence already exists; do not restart")
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("private model key and database URL are required")
    first_audit = await reconcile(
        execution_root,
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
    )
    # Read-only permission qualification established that 022 makes freshly
    # compiled scripts readable to the image's non-root user.  Original build
    # artifacts remain untouched; private evidence writers use explicit 0600.
    os.umask(0o022)
    endpoint = _endpoint(manifest)
    recheck_sha = _write_json(private_dir / "endpoint-recheck-cell-1.json", endpoint)
    _write_json(
        clearance_path,
        {
            "protocol": manifest["protocol"],
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "original_journal_sha256": STOP_JOURNAL_SHA256,
            "retained_first_cell": first_audit,
            "continuation_approval_sha256": EXPECTED_CONTINUATION_APPROVAL_SHA256,
            "endpoint_recheck_sha256": recheck_sha,
            "next_cell_order": 2,
            "permission_qualification": "fresh no-model range healthy with umask 022",
        },
    )
    _write_bytes(continuation_path, b"")
    journal = Journal(
        continuation_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=(REVISION, UPSTREAM),
        max_estimated_usd=float(CAP),
    )
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    runtime = ComposeRangeRuntime(state_dir)
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_recovery_pinned_analysis",
    )
    guard = EndpointGuard()
    records: list[dict] = []
    audits: list[dict] = []
    committed_cost = Decimal(0)
    conservative_cost = Decimal(0)
    try:
        for pair_index in range(48):
            pair = manifest["cells"][2 * pair_index : 2 * pair_index + 2]
            if (
                pair[0]["seed"] != pair[1]["seed"]
                or pair[0]["checkpoint_id"] != pair[1]["checkpoint_id"]
                or {pair[0]["variant"], pair[1]["variant"]} != {"vulnerable", "patched"}
            ):
                raise ValueError("frozen pair structure differs")
            if pair_index == 0:
                pair_audits = [first_audit]
                endpoint_sha = recheck_sha
                pair_to_run = pair[1:]
            else:
                endpoint_sha = _write_json(
                    private_dir / f"endpoint-preflight-{pair_index + 1}.json", _endpoint(manifest)
                )
                pair_audits = []
                pair_to_run = pair
            for cell in pair_to_run:
                if _sha256(journal_path.read_bytes()) != STOP_JOURNAL_SHA256:
                    raise ValueError("original stopped journal changed")
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
                await _inventory_with_retained_first(engine, events, records, audits)
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
                    "run_ids": [str(STOP_RUN_ID), records[0]["run_id"]]
                    if pair_index == 0
                    else [item["run_id"] for item in records[-2:]],
                    "audits": pair_audits,
                    "endpoint_preflight_sha256": endpoint_sha,
                    "original_pair1_preflight_sha256": first_audit["endpoint_preflight_sha256"]
                    if pair_index == 0
                    else None,
                    "cumulative_estimated_cost_usd": str(committed_cost),
                    "conservative_cost_usd": str(conservative_cost),
                },
            )
        return {
            "completed_assigned_cells": 96,
            "newly_executed_cells": len(records),
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
