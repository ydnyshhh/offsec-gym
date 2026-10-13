"""Fail-closed continuation for only frozen cells 87–96 after cell 86.

Cell 86 remains outcome-missing with its provider reservation unresolved. The
approval and merge pins are intentionally zero until a separate human decision.
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

from m661_cell86_interrupted_stop import (
    INTERRUPTED_IDENTITY_SHA256,
    INTERRUPTED_RUN_ID,
    INTERRUPTED_TRACE_SHA256,
    RECORDED_USED_COST_MICROUSD,
    STOP_COMPLETED_CONSERVATIVE_COST,
    STOP_COMPLETED_ESTIMATED_COST,
    STOP_JOURNAL_SHA256,
    STOP_LOG_SHA256,
    UNSETTLED_CALL_ID,
    UNSETTLED_RESERVED_COST_MICROUSD,
    _completed_prefix,
    admit,
)
from m661_cell86_recovery_postcheck import interrupted_pair_audit, postcheck
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
from m661_resume_after_cell14_pause import EXPECTED_CELL14_APPROVAL_SHA256
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.critical_checkpoint import CriticalCheckpointExperimentRunner
from offsecgym.research.m64_execute import Journal
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.storage.event_store import PostgresEventStore

CONTINUATION_SCOPE = "m661_feasibility_cells_87_through_96_once_after_cell86_interruption"
EXPECTED_CELL86_APPROVAL_SHA256 = "a6dbed788b34ad31113de65b5992a66c81c03eeb90bc84e9232331f7b184c3c9"
OPERATIONAL_MERGE_COMMIT = "bd909a702eaea06b3966430522f9e393f5f7a238"


def _scope_approval(path: Path, manifest: dict) -> dict:
    if EXPECTED_CELL86_APPROVAL_SHA256 == "0" * 64 or OPERATIONAL_MERGE_COMMIT == "0" * 40:
        raise ValueError("cell-86 paid continuation is closed pending exact human approval")
    if path.name != "cell86-paid-continuation-approval.json" or path.is_symlink():
        raise ValueError("cell-86 approval path differs")
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("cell-86 approval is absent or not private")
    raw = path.read_bytes()
    if _sha256(raw) != EXPECTED_CELL86_APPROVAL_SHA256:
        raise ValueError("cell-86 approval bytes differ")
    approval = json.loads(raw)
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    if (
        approved_at.utcoffset() is None
        or approved_at <= datetime(2026, 10, 13, 16, 30, tzinfo=UTC)
        or approval.get("approved_by") != "human_user"
        or approval.get("approved_for_paid_calls") is not True
        or approval.get("approval_scope") != CONTINUATION_SCOPE
        or approval.get("protocol") != manifest["protocol"]
        or approval.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("source_commit") != SOURCE_COMMIT
        or approval.get("protocol_commit") != PROTOCOL_COMMIT
        or approval.get("operational_merge_commit") != OPERATIONAL_MERGE_COMMIT
        or approval.get("prior_paid_approval_sha256") != EXPECTED_CELL14_APPROVAL_SHA256
        or approval.get("assignment_sha256") != manifest["schedule"]["cell_assignment_sha256"]
        or approval.get("stopped_journal_sha256") != STOP_JOURNAL_SHA256
        or approval.get("stopped_log_sha256") != STOP_LOG_SHA256
        or approval.get("interrupted_run_id") != str(INTERRUPTED_RUN_ID)
        or approval.get("interrupted_trace_sha256") != INTERRUPTED_TRACE_SHA256
        or approval.get("interrupted_event_identity_sha256") != INTERRUPTED_IDENTITY_SHA256
        or approval.get("unsettled_call_id") != str(UNSETTLED_CALL_ID)
        or approval.get("unsettled_reservation_microusd") != UNSETTLED_RESERVED_COST_MICROUSD
        or approval.get("interrupted_cell_conservative_usd") != str(CELL_RESERVATION)
        or approval.get("first_paid_cell_order") != 87
        or approval.get("last_paid_cell_order") != 96
        or approval.get("paid_cells_authorized") != 10
        or approval.get("retained_missing_orders") != [1, 4, 86]
        or approval.get("retained_provider_failed_orders") != [13, 14]
        or approval.get("provider_health_epoch_start_order") != 15
        or approval.get("no_retry_or_replacement") is not True
        or Decimal(str(approval.get("cumulative_cost_ceiling_usd"))) != CAP
    ):
        raise ValueError("cell-86 approval does not bind exact stopped scope")
    return approval


def _no_collector_running() -> None:
    output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or int(fields[0]) == os.getpid():
            continue
        executable = fields[1].split(None, 1)[0].rsplit("/", 1)[-1]
        if "python" in executable and any(
            name in fields[1]
            for name in (
                "m661_feasibility_collect.py",
                "m661_resume_after_cell1_permission.py",
                "m661_resume_after_cell4_permission.py",
                "m661_resume_after_cell14_pause.py",
                "m661_resume_after_cell86_permission.py",
            )
        ):
            raise ValueError("another M6.6.1 collector is running")


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
    private = approval_path.parent
    if continuation_approval_path.parent != private:
        raise ValueError("continuation approval directory differs")
    _scope_approval(continuation_approval_path, manifest)
    _no_collector_running()
    third_path = private / "cell86-paid-continuation-journal.jsonl"
    clearance_path = private / "cell86-paid-clearance.json"
    pair43_path = private / "pair-postcheck-43.json"
    if any(path.exists() for path in (third_path, clearance_path, pair43_path)):
        raise ValueError("cell-86 continuation evidence exists; do not restart")
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
    # Pair 43 closes from the score-valid cell 85 and cell 86's exact retained
    # prefix. No provider call or synthetic terminal event is used.
    previous, _ = _completed_prefix(manifest, private)
    cell85 = manifest["cells"][84]
    cell86 = manifest["cells"][85]
    record85 = previous[-1]
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    store = PostgresEventStore(engine)
    state = ComposeRangeRuntime(state_dir).state
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_cell86_continuation_analysis",
    )
    try:
        audit85 = await _audit_cell(store, state, analysis, cell85, record85, None)
    finally:
        await engine.dispose()
    preflight43_raw = (private / "endpoint-preflight-43.json").read_bytes()
    selected43 = json.loads(preflight43_raw)
    if (
        selected43.get("endpoint") != f"{UPSTREAM} | {REVISION}"
        or selected43.get("input_usd_per_million_tokens") != 3.0
        or selected43.get("output_usd_per_million_tokens") != 15.0
    ):
        raise ValueError("retained pair-43 selected endpoint differs")
    committed_cost = (
        STOP_COMPLETED_ESTIMATED_COST + Decimal(RECORDED_USED_COST_MICROUSD) / 1_000_000
    )
    conservative_cost = STOP_COMPLETED_CONSERVATIVE_COST + CELL_RESERVATION
    _write_json(
        pair43_path,
        {
            "protocol": manifest["protocol"],
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "pair_number": 43,
            "cell_ids": [cell85["cell_id"], cell86["cell_id"]],
            "run_ids": [record85["run_id"], str(INTERRUPTED_RUN_ID)],
            "audits": [audit85, interrupted_pair_audit()],
            "endpoint_preflight_sha256": _sha256(preflight43_raw),
            "original_pair1_preflight_sha256": None,
            "cumulative_estimated_cost_usd": str(committed_cost),
            "conservative_cost_usd": str(conservative_cost),
        },
    )
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
    os.umask(0o022)
    selected = _endpoint(manifest)
    if any(
        selected.get(field) != selected43.get(field)
        for field in ("endpoint", "input_usd_per_million_tokens", "output_usd_per_million_tokens")
    ):
        raise ValueError("selected endpoint or price drifted")
    recheck_sha = _write_json(private / "endpoint-recheck-cell-86.json", selected)
    _write_json(
        clearance_path,
        {
            "protocol": manifest["protocol"],
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "stopped_journal_sha256": STOP_JOURNAL_SHA256,
            "pair43_receipt_sha256": _sha256(pair43_path.read_bytes()),
            "continuation_approval_sha256": EXPECTED_CELL86_APPROVAL_SHA256,
            "endpoint_recheck_sha256": recheck_sha,
            "retained_missing_orders": [1, 4, 86],
            "retained_provider_failed_orders": [13, 14],
            "provider_health_epoch_start_order": 15,
            "next_cell_order": 87,
        },
    )
    _write_bytes(third_path, b"")
    journal = Journal(
        third_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=(REVISION, UPSTREAM),
        max_estimated_usd=float(CAP),
    )
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
    guard = EndpointGuard()
    health_epoch = previous.copy()
    try:
        for pair_index in range(43, 48):
            pair = manifest["cells"][2 * pair_index : 2 * pair_index + 2]
            if (
                pair[0]["seed"] != pair[1]["seed"]
                or pair[0]["checkpoint_id"] != pair[1]["checkpoint_id"]
                or {pair[0]["variant"], pair[1]["variant"]} != {"vulnerable", "patched"}
            ):
                raise ValueError("frozen pair structure differs")
            endpoint_sha = _write_json(
                private / f"endpoint-preflight-{pair_index + 1}.json", _endpoint(manifest)
            )
            pair_audits = []
            pair_records = []
            for cell in pair:
                if (
                    _sha256((private / "cell14-paid-continuation-journal.jsonl").read_bytes())
                    != STOP_JOURNAL_SHA256
                ):
                    raise ValueError("interrupted journal changed")
                if conservative_cost + CELL_RESERVATION > CAP:
                    raise ValueError("next frozen cell exceeds cumulative approved cap")
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
                stage_path = private / "stages" / f"{cell['cell_id']}.json"
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
                trace_path = private / "traces" / f"{cell['cell_id']}.json"
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
                pair_records.append(record)
                pair_audits.append(audited)
                health_epoch.append(record)
                committed_cost += Decimal(str(record["estimated_cost_usd"]))
                conservative_cost += (
                    Decimal(str(record["estimated_cost_usd"]))
                    if record["resource_auditable"]
                    else CELL_RESERVATION
                )
                if len(pair_records) == 2:
                    _write_json(
                        private / f"pair-postcheck-{pair_index + 1}.json",
                        {
                            "protocol": manifest["protocol"],
                            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                            "pair_number": pair_index + 1,
                            "cell_ids": [item["cell_id"] for item in pair],
                            "run_ids": [item["run_id"] for item in pair_records],
                            "audits": pair_audits,
                            "endpoint_preflight_sha256": endpoint_sha,
                            "original_pair1_preflight_sha256": None,
                            "cumulative_estimated_cost_usd": str(committed_cost),
                            "conservative_cost_usd": str(conservative_cost),
                        },
                    )
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
                if _health_decision(health_epoch) != "continue":
                    raise ValueError("frozen provider-health gate requires study stop")
        result = await postcheck(
            execution_root,
            source_root,
            protocol_root,
            manifest_path,
            approval_path,
            journal_path,
            state_dir,
            require_complete=True,
        )
        return result
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
