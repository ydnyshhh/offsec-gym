"""Close retained pair seven and resume only frozen M6.6.1 cells 15–96.

Both provider failures and both infrastructure-missing assignments remain in
their original journals. This file is closed until a new human approval hash
is pinned in a separate provenance commit.
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
from uuid import uuid4

from m661_cell1_recovery import STOP_JOURNAL_SHA256 as ORIGINAL_JOURNAL_SHA256
from m661_cell1_recovery import STOP_RUN_ID
from m661_cell4_recovery_postcheck import _new_records
from m661_cell4_stop import (
    CELL4_RUN_ID,
    CONTINUATION_JOURNAL_SHA256,
    CONTINUATION_LOG_SHA256,
    _stopped_records,
)
from m661_cell14_provider_stop import (
    FAILED_RUNS,
    PREFLIGHT7_SHA256,
    STOP_CONSERVATIVE_COST,
    STOP_ESTIMATED_COST,
    STOP_JOURNAL_SHA256,
    STOP_LOG_SHA256,
    admit,
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
from m661_resume_after_cell4_permission import (
    EXPECTED_PAID_APPROVAL_SHA256 as PRIOR_PAID_APPROVAL_SHA256,
)
from m661_resume_after_cell4_permission import (
    _inventory_with_retained_missing,
    _no_collector_running,
)
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.critical_checkpoint import CriticalCheckpointExperimentRunner
from offsecgym.research.m64_execute import Journal
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.storage.event_store import PostgresEventStore

CONTINUATION_SCOPE = "m661_feasibility_cells_15_through_96_once_after_cell14_provider_pause"
EXPECTED_CELL14_APPROVAL_SHA256 = "0" * 64
OPERATIONAL_MERGE_COMMIT = "0" * 40


def _scope_approval(path: Path, manifest: dict) -> dict:
    if EXPECTED_CELL14_APPROVAL_SHA256 == "0" * 64 or OPERATIONAL_MERGE_COMMIT == "0" * 40:
        raise ValueError("cell-14 paid continuation remains closed pending separate human approval")
    if path.name != "cell14-paid-continuation-approval.json" or path.is_symlink():
        raise ValueError("continuation approval path differs")
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("continuation approval is absent or not private")
    raw = path.read_bytes()
    if _sha256(raw) != EXPECTED_CELL14_APPROVAL_SHA256:
        raise ValueError("continuation approval bytes differ")
    approval = json.loads(raw)
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    if (
        approved_at.utcoffset() is None
        or approved_at <= datetime(2026, 10, 12, 22, 34, tzinfo=UTC)
        or approval.get("approved_by") != "human_user"
        or approval.get("approved_for_paid_calls") is not True
        or approval.get("approval_scope") != CONTINUATION_SCOPE
        or approval.get("protocol") != manifest["protocol"]
        or approval.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("source_commit") != SOURCE_COMMIT
        or approval.get("protocol_commit") != PROTOCOL_COMMIT
        or approval.get("operational_merge_commit") != OPERATIONAL_MERGE_COMMIT
        or approval.get("prior_paid_approval_sha256") != PRIOR_PAID_APPROVAL_SHA256
        or approval.get("assignment_sha256") != manifest["schedule"]["cell_assignment_sha256"]
        or approval.get("original_journal_sha256") != ORIGINAL_JOURNAL_SHA256
        or approval.get("stopped_continuation_journal_sha256") != CONTINUATION_JOURNAL_SHA256
        or approval.get("stopped_continuation_log_sha256") != CONTINUATION_LOG_SHA256
        or approval.get("stopped_cell4_paid_journal_sha256") != STOP_JOURNAL_SHA256
        or approval.get("stopped_cell4_paid_log_sha256") != STOP_LOG_SHA256
        or approval.get("pair7_preflight_sha256") != PREFLIGHT7_SHA256
        or approval.get("retained_cell1_run_id") != str(STOP_RUN_ID)
        or approval.get("retained_cell4_run_id") != str(CELL4_RUN_ID)
        or approval.get("retained_provider_failed_run_ids") != list(FAILED_RUNS)
        or approval.get("first_paid_cell_order") != 15
        or approval.get("last_paid_cell_order") != 96
        or approval.get("paid_cells_authorized") != 82
        or approval.get("retained_missing_orders") != [1, 4]
        or approval.get("retained_provider_failed_orders") != [13, 14]
        or approval.get("new_provider_health_epoch_start_order") != 15
        or approval.get("stopped_estimated_cost_usd") != str(STOP_ESTIMATED_COST)
        or approval.get("stopped_conservative_cost_usd") != str(STOP_CONSERVATIVE_COST)
        or approval.get("no_retry_or_replacement") is not True
        or Decimal(str(approval.get("cumulative_cost_ceiling_usd"))) != CAP
    ):
        raise ValueError("continuation approval does not bind exact stopped scope")
    return approval


def _no_new_collector_running() -> None:
    _no_collector_running()
    output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or int(fields[0]) == os.getpid():
            continue
        executable = fields[1].split(None, 1)[0].rsplit("/", 1)[-1]
        if "python" in executable and "m661_resume_after_cell14_pause.py" in fields[1]:
            raise ValueError("another M6.6.1 provider-pause collector is running")


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
    _no_new_collector_running()
    private_dir = journal_path.parent
    stopped_path = private_dir / "cell4-paid-continuation-journal.jsonl"
    continuation_path = private_dir / "cell14-paid-continuation-journal.jsonl"
    clearance_path = private_dir / "cell14-provider-clearance.json"
    if (
        continuation_path.exists()
        or clearance_path.exists()
        or (private_dir / "endpoint-recheck-cell-14.json").exists()
        or (private_dir / "pair-postcheck-7.json").exists()
    ):
        raise ValueError("continuation evidence already exists; do not restart")
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("private model key and database URL are required")
    await admit(
        execution_root,
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
    )
    # Pair seven was interrupted after two assigned provider failures. Rebuild
    # its receipt solely from the retained PostgreSQL events, without a call.
    pair_seven = manifest["cells"][12:14]
    old_records, _ = _stopped_records(manifest, private_dir)
    first_records = _new_records(manifest, stopped_path)
    records = [*old_records, *first_records]
    preflight_seven_raw = (private_dir / "endpoint-preflight-7.json").read_bytes()
    selected_seven = json.loads(preflight_seven_raw)
    if (
        _sha256(preflight_seven_raw) != PREFLIGHT7_SHA256
        or selected_seven.get("endpoint") != f"{UPSTREAM} | {REVISION}"
        or selected_seven.get("input_usd_per_million_tokens") != 3.0
        or selected_seven.get("output_usd_per_million_tokens") != 15.0
    ):
        raise ValueError("retained pair-seven endpoint differs")
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    runtime = ComposeRangeRuntime(state_dir)
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_recovery_pinned_analysis",
    )
    try:
        pair_seven_audits = [
            await _audit_cell(events, runtime.state, analysis, cell, record, None)
            for cell, record in zip(pair_seven, first_records[-2:], strict=True)
        ]
    finally:
        await engine.dispose()
    _write_json(
        private_dir / "pair-postcheck-7.json",
        {
            "protocol": manifest["protocol"],
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "pair_number": 7,
            "cell_ids": [cell["cell_id"] for cell in pair_seven],
            "run_ids": [record["run_id"] for record in first_records[-2:]],
            "audits": pair_seven_audits,
            "endpoint_preflight_sha256": PREFLIGHT7_SHA256,
            "original_pair1_preflight_sha256": None,
            "cumulative_estimated_cost_usd": str(STOP_ESTIMATED_COST),
            "conservative_cost_usd": str(STOP_CONSERVATIVE_COST),
        },
    )
    # The existing postcheck verifies retrospective pair closure before any
    # new clearance or model call is permitted.
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
    # Read-only permission qualification established that 022 makes freshly
    # compiled scripts readable to the image's non-root user.  Original build
    # artifacts remain untouched; private evidence writers use explicit 0600.
    os.umask(0o022)
    endpoint = _endpoint(manifest)
    if any(
        endpoint.get(field) != selected_seven.get(field)
        for field in (
            "endpoint",
            "input_usd_per_million_tokens",
            "output_usd_per_million_tokens",
        )
    ):
        raise ValueError("selected endpoint differs from retained pair-seven preflight")
    recheck_sha = _write_json(private_dir / "endpoint-recheck-cell-14.json", endpoint)
    _write_json(
        clearance_path,
        {
            "protocol": manifest["protocol"],
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "stopped_journal_sha256": STOP_JOURNAL_SHA256,
            "pair7_receipt_sha256": _sha256((private_dir / "pair-postcheck-7.json").read_bytes()),
            "continuation_approval_sha256": EXPECTED_CELL14_APPROVAL_SHA256,
            "endpoint_recheck_sha256": recheck_sha,
            "retained_missing_orders": [1, 4],
            "retained_provider_failed_orders": [13, 14],
            "new_provider_health_epoch_start_order": 15,
            "next_cell_order": 15,
        },
    )
    _write_bytes(continuation_path, b"")
    journal = Journal(
        continuation_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=(REVISION, UPSTREAM),
        max_estimated_usd=float(CAP),
    )
    # Replay the new clearance and empty journal before the first paid call.
    await postcheck(
        execution_root,
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
        require_complete=False,
        second_journal_path=continuation_path,
    )
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    runtime = ComposeRangeRuntime(state_dir)
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_recovery_pinned_analysis",
    )
    guard = EndpointGuard()
    audits: list[dict] = [
        json.loads((private_dir / "pair-postcheck-1.json").read_bytes())["audits"][1],
        json.loads((private_dir / "pair-postcheck-2.json").read_bytes())["audits"][0],
    ]
    for number in range(3, 8):
        audits.extend(
            json.loads((private_dir / f"pair-postcheck-{number}.json").read_bytes())["audits"]
        )
    if len(records) != 12 or len(audits) != 12:
        raise ValueError("retained audited source inventory differs")
    committed_cost = STOP_ESTIMATED_COST
    conservative_cost = STOP_CONSERVATIVE_COST
    health_epoch_records: list[dict] = []
    try:
        for pair_index in range(7, 48):
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
                if _sha256(journal_path.read_bytes()) != ORIGINAL_JOURNAL_SHA256:
                    raise ValueError("original stopped journal changed")
                stopped_raw = (private_dir / "continuation-journal.jsonl").read_bytes()
                if _sha256(stopped_raw) != CONTINUATION_JOURNAL_SHA256:
                    raise ValueError("stopped continuation journal changed")
                if _sha256(stopped_path.read_bytes()) != STOP_JOURNAL_SHA256:
                    raise ValueError("cell-four paid journal changed")
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
                health_epoch_records.append(record)
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
                if _health_decision(health_epoch_records) != "continue":
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
            "newly_executed_cells": len(records) - 12,
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
