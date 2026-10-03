"""Reconcile one completed M6.5 control run that stopped at the coverage gate.

This makes no model call and never retries a cell. It closes only monolithic
semantic coverage claims after RunCompleted, then reconstructs the exact score
and journal record from authoritative events and verified artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import evaluate_run
from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.research.m64_analysis import M64Observation
from offsecgym.research.m64_execute import _sha256, _utc_now, _write_trace
from offsecgym.research.m65_monolithic_execute import (
    INPUT_USD_PER_MILLION,
    OUTPUT_USD_PER_MILLION,
    verify_manifest,
)
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import ValidationContext
from offsecgym.schemas.events import (
    ActionRequested,
    CoverageLeaseAcquired,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelReservationRejected,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    RunCompleted,
    RunStarted,
    WorkerSpawned,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events
from offsecgym.worldview import EventWorldState


async def reconcile(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    state_dir: Path,
    cell_id: str,
    database_url: str,
) -> dict[str, object]:
    manifest = verify_manifest(root, manifest_path)
    cell = next((item for item in manifest["cells"] if item["cell_id"] == cell_id), None)
    if cell is None:
        raise ValueError("cell is outside the frozen manifest")
    with journal_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        records = [json.loads(line) for line in handle if line.strip()]
        if not records or records[0]["manifest_sha256"] != _sha256(manifest_path.read_bytes()):
            raise ValueError("journal does not match frozen manifest")
        if records[-1].get("type") != "cell_started" or records[-1].get("cell_id") != cell_id:
            raise ValueError("the requested cell is not the sole interrupted tail")
        if (
            sum(item["type"] == "cell_started" and item["cell_id"] == cell_id for item in records)
            != 1
        ):
            raise ValueError("cell has more than one start")
        completed = [item for item in records if item["type"] == "cell_completed"]
        if len(completed) + 1 != cell["order"]:
            raise ValueError("interrupted cell is out of manifest order")
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                rows = (
                    (
                        await conn.execute(
                            text(
                                "select distinct run_id from events where type='run_started' "
                                "and payload->>'experiment_hash'=:hash"
                            ),
                            {"hash": cell["experiment_sha256"]},
                        )
                    )
                    .scalars()
                    .all()
                )
            if len(rows) != 1:
                raise ValueError("interrupted cell lacks one authoritative run")
            run_id: UUID = rows[0]
            events = PostgresEventStore(engine)
            trace = await events.read_run(run_id)
            starts = [item for item in trace if isinstance(item, RunStarted)]
            ranges = [item for item in trace if isinstance(item, RangeStarted)]
            endings = [item for item in trace if isinstance(item, RunCompleted)]
            if len(starts) != 1 or len(ranges) != 1 or len(endings) != 1:
                raise ValueError("run lifecycle is incomplete")
            if str(ranges[0].build_id) != cell["build_id"] or endings[0].status not in {
                "completed",
                "budget_exhausted",
                "agent_failed",
            }:
                raise ValueError("build or scoreable terminal status disagrees")
            bootstrap = [item for item in trace if isinstance(item, PrerequisiteBootstrapCompleted)]
            bootstrap_actions = [
                item
                for item in trace
                if isinstance(item, ActionRequested) and item.source_phase == "bootstrap"
            ]
            if (
                len(bootstrap) != 1
                or bootstrap[0].action_count != 17
                or len(bootstrap_actions) != 17
            ):
                raise ValueError("bootstrap gate failed")
            projection = project_controller_events(trace)
            if (
                projection.active_workers
                or projection.active_actions
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError("noncoverage controller reservations remain")
            if any(isinstance(item, WorkerSpawned) for item in trace):
                raise ValueError("coverage cleanup applies only to monolithic runs")
            acquired = {
                item.claim_id: item for item in trace if isinstance(item, CoverageLeaseAcquired)
            }
            if set(projection.active_coverage) - set(acquired):
                raise ValueError("coverage projection lacks acquisition provenance")
            world = EventWorldState(events)
            for claim_id, task_id in projection.active_coverage.items():
                if acquired[claim_id].task_id != task_id:
                    raise ValueError("coverage lease owner mismatch")
                await world.update_coverage(run_id, claim_id, task_id, "released")
            trace = await events.read_run(run_id)
            projection = project_controller_events(trace)
            if (
                projection.active_workers
                or projection.active_actions
                or projection.active_coverage
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError("controller reservations remain after reconciliation")
            calls = [item for item in trace if isinstance(item, ModelCallCompleted)]
            endpoint = manifest["expected_selected_endpoint"]
            expected = endpoint["revision"], endpoint["upstream_provider"]
            if calls and {
                (item.resolved_model_revision, item.resolved_upstream_provider) for item in calls
            } != {expected}:
                raise ValueError("selected model endpoint drifted")
            findings = tuple(item.finding for item in trace if isinstance(item, FindingSubmitted))
            validations = tuple(item.result for item in trace if isinstance(item, FindingValidated))
            context = ValidationContext(
                run_id=run_id,
                range_instance_id=ranges[0].range_instance_id,
                range_generation=ranges[0].range_generation,
                build_id=ranges[0].build_id,
            )
            evaluation = evaluate_run(
                findings,
                validations,
                StateOracleStore(StateStore(state_dir)).load_for_context(context),
                status=endings[0].status,
            )
            if not evaluation.score_valid:
                raise ValueError("reconciled run is unscored")
            input_tokens = sum(item.input_tokens for item in calls)
            output_tokens = sum(item.output_tokens for item in calls)
            cost = (
                input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION
            ) / 1_000_000
            spent = sum(item["estimated_cost_usd"] for item in completed)
            if spent + cost > records[0]["max_estimated_usd"]:
                raise ValueError("reconciled cost exceeds approved threshold")
            observation = M64Observation(
                cell_id=cell_id,
                experiment_sha256=cell["experiment_sha256"],
                run_id=run_id,
                evaluation=evaluation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                resolved_model_revision=expected[0],
                upstream_provider=expected[1],
            )
            trace_path = journal_path.parent / "traces" / f"{cell_id}.json"
            trace_sha = _write_trace(trace_path, trace)
            rejected = [item for item in trace if isinstance(item, ModelReservationRejected)]
            record = {
                "type": "cell_completed",
                "cell_id": cell_id,
                "order": cell["order"],
                "at": _utc_now(),
                "duration_seconds": round(
                    (endings[0].occurred_at - starts[0].occurred_at).total_seconds(), 3
                ),
                "build_id": cell["build_id"],
                "estimated_cost_usd": round(cost, 8),
                "trace_path": str(trace_path),
                "trace_sha256": trace_sha,
                "observation": observation.model_dump(mode="json"),
                "orchestration": orchestration_metrics(trace).model_dump(mode="json"),
                "failure_reason": rejected[-1].reason_code if rejected else None,
                "reconciliation": {
                    "kind": "postrun_monolithic_coverage_release_v1",
                    "released_claims": len(acquired),
                    "authoritative_run_id": str(run_id),
                    "run_completed_sequence": endings[0].sequence_number,
                    "final_sequence": trace[-1].sequence_number,
                },
            }
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            return {
                "cell_id": cell_id,
                "run_id": str(run_id),
                "score_valid": evaluation.score_valid,
                "estimated_cost_usd": cost,
            }
        finally:
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--cell-id", required=True)
    args = parser.parse_args()
    database_url = os.environ.get("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    result = asyncio.run(
        reconcile(
            args.repository_root,
            args.manifest,
            args.journal,
            args.state_dir,
            args.cell_id,
            database_url,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
