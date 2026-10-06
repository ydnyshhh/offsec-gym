"""Reconcile the already-executed M6.6 cell 2; never call a model or range."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from m66_collect_pilot import _approval_and_manifest, _record, _spec
from m66_pilot_coverage import close_completed_monolithic_coverage
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import evaluate_run
from offsecgym.research.m64_execute import _sha256, _utc_now, _write_trace
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import (
    FindingSubmitted,
    FindingValidated,
    RangeStarted,
    RunCompleted,
    WorkerSpawned,
)
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events

CELL_ID = "55e8cfa8bb10ab32"
FIRST_CELL_ID = "8c0c378b543a8b70"
FIRST_RUN_ID = UUID("3e492a4e-704f-471d-a968-45aa4a383ab4")
SECOND_RUN_ID = UUID("e11a07a5-4557-4b69-ab79-f807901d7a35")
STOP_JOURNAL_SHA256 = "6e8e0c909482301b20368801593080af4b7e5d0a16f72f9e5ad4dd495ee77261"
PRE_CLOSURE_EVENT_COUNT = 400


def _event_identity_hash(trace: list) -> str:
    identities = [[event.sequence_number, str(event.event_id)] for event in trace]
    return hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest()


async def reconcile(
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, _approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, approval_path
    )
    if not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    if [cell["cell_id"] for cell in manifest["cells"][:2]] != [FIRST_CELL_ID, CELL_ID]:
        raise ValueError("frozen first-pair order differs")
    with journal_path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        before_bytes = journal_path.read_bytes()
        if _sha256(before_bytes) != STOP_JOURNAL_SHA256:
            raise ValueError("stopped journal differs; no reconciliation mutation allowed")
        rows = [json.loads(line) for line in before_bytes.splitlines()]
        if (
            len(rows) != 4
            or [row["type"] for row in rows]
            != ["batch_started", "cell_started", "cell_started", "cell_completed"]
            or rows[1]["cell_id"] != FIRST_CELL_ID
            or rows[2]["cell_id"] != CELL_ID
            or rows[3]["cell_id"] != FIRST_CELL_ID
            or rows[3]["run_id"] != str(FIRST_RUN_ID)
        ):
            raise ValueError("stopped journal shape differs")
        first = rows[3]
        first_trace = Path(first["trace_path"])
        if not first_trace.is_file() or _sha256(first_trace.read_bytes()) != first["trace_sha256"]:
            raise ValueError("first cell's journaled trace differs")
        engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
        try:
            events = PostgresEventStore(engine)
            trace = await events.read_run(SECOND_RUN_ID)
            if (
                len(trace) != PRE_CLOSURE_EVENT_COUNT
                or [event.sequence_number for event in trace]
                != list(range(1, PRE_CLOSURE_EVENT_COUNT + 1))
                or any(event.run_id != SECOND_RUN_ID for event in trace)
                or sum(isinstance(event, RunCompleted) for event in trace) != 1
                or any(isinstance(event, WorkerSpawned) for event in trace)
            ):
                raise ValueError("cell 2 authoritative preclosure trace differs")
            terminal = next(event for event in trace if isinstance(event, RunCompleted))
            if (
                terminal.status != "budget_exhausted"
                or terminal.sequence_number != PRE_CLOSURE_EVENT_COUNT
            ):
                raise ValueError("cell 2 terminal state differs")
            ranges = [event for event in trace if isinstance(event, RangeStarted)]
            if len(ranges) != 1 or str(ranges[0].build_id) != first["build_id"]:
                raise ValueError("cell 2 did not use the first cell's frozen build")
            build = ComposeRangeRuntime(state_dir).state.verify_build_integrity(ranges[0].build_id)
            if str(build.pair_id) != first["pair_id"]:
                raise ValueError("cell 2 pair identifier differs")
            projection = project_controller_events(trace)
            if (
                len(projection.active_coverage) != 1
                or projection.active_workers
                or projection.active_actions
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError("cell 2 has unexpected active reservations")
            claim_id = next(iter(projection.active_coverage))
            oracle = GroundTruthManifest.model_validate_json(
                (state_dir / "oracles" / ranges[0].build_id.hex / "ground_truth.json").read_bytes()
            )
            submissions = [event.finding for event in trace if isinstance(event, FindingSubmitted)]
            results = {
                event.result.finding_id: event.result
                for event in trace
                if isinstance(event, FindingValidated)
            }
            if set(results) != {finding.finding_id for finding in submissions}:
                raise ValueError("cell 2 validations do not match submitted findings")
            evaluation = evaluate_run(
                tuple(submissions),
                tuple(results[finding.finding_id] for finding in submissions),
                oracle,
                status="budget_exhausted",
            )
            cell = manifest["cells"][1]
            spec = _spec(protocol_root, manifest, cell)
            trace_path = journal_path.parent / "traces" / f"{CELL_ID}.json"
            if trace_path.exists():
                raise ValueError("cell 2 final trace already exists")
            receipt_path = journal_path.parent / "cell2-reconciliation.json"
            if receipt_path.exists():
                raise ValueError("cell 2 reconciliation receipt already exists")
            pre_path = journal_path.parent / "cell2-preclosure-trace.json"
            if pre_path.exists():
                raise ValueError("preclosure trace snapshot already exists")
            pre_sha = _write_trace(pre_path, trace)
            before_identity_hash = _event_identity_hash(trace)
            released = await close_completed_monolithic_coverage(events, SECOND_RUN_ID)
            if released != 1:
                raise ValueError("cell 2 coverage release count differs")
            after = await events.read_run(SECOND_RUN_ID)
            if (
                len(after) != PRE_CLOSURE_EVENT_COUNT + 2
                or [event.event_id for event in after[:PRE_CLOSURE_EVENT_COUNT]]
                != [event.event_id for event in trace]
                or [event.type for event in after[-2:]]
                != ["coverage_updated", "coverage_lease_released"]
                or project_controller_events(after).active_coverage
            ):
                raise ValueError("cell 2 postclosure event stream differs")
            outcome = SimpleNamespace(
                run_id=SECOND_RUN_ID,
                build_id=ranges[0].build_id,
                evaluation=evaluation,
                failure_reason=None,
            )
            record = _record(
                cell=cell,
                order=2,
                outcome=outcome,
                spec=spec,
                trace=after,
                build=build,
                trace_path=trace_path,
            )
            if (
                not record["score_valid"]
                or record["status"] != "budget_exhausted"
                or record["build_id"] != first["build_id"]
                or record["pair_id"] != first["pair_id"]
            ):
                raise ValueError("reconciled cell 2 differs from the frozen pair")
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            receipt = {
                "protocol": manifest["protocol"],
                "cell_id": CELL_ID,
                "run_id": str(SECOND_RUN_ID),
                "reconciled_at": _utc_now(),
                "stopped_journal_sha256": STOP_JOURNAL_SHA256,
                "reconciled_journal_sha256": _sha256(journal_path.read_bytes()),
                "preclosure_event_count": PRE_CLOSURE_EVENT_COUNT,
                "preclosure_event_identity_sha256": before_identity_hash,
                "preclosure_trace_sha256": pre_sha,
                "coverage_claim_id": str(claim_id),
                "postclosure_event_count": len(after),
                "postclosure_event_identity_sha256": _event_identity_hash(after),
                "postclosure_trace_sha256": record["trace_sha256"],
                "score_replay": evaluation.model_dump(mode="json"),
                "estimated_cost_usd": record["estimated_cost_usd"],
                "model_calls_after_terminal": 0,
                "gateway_actions_after_terminal": 0,
                "retry_count": 0,
            }
            with receipt_path.open("x", encoding="utf-8") as stream:
                json.dump(receipt, stream, sort_keys=True, indent=2)
                stream.write("\n")
            return receipt
        finally:
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        reconcile(
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(
        json.dumps(
            {
                "cell_id": result["cell_id"],
                "reconciled_journal_sha256": result["reconciled_journal_sha256"],
                "estimated_cost_usd": result["estimated_cost_usd"],
                "retry_count": result["retry_count"],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
