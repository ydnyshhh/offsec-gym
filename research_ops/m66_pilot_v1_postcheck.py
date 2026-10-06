"""Read-only authoritative closeout of the stopped M6.6 v1 pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m66_collect_pilot import _approval_and_manifest, _spec
from m66_reconcile_bootstrap_failure import _expected_run_hash, _identity_hash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import evaluate_run, unscored_run
from offsecgym.research.m64_execute import _sha256
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import (
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallStarted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events
from offsecgym.storage.tables import runs

FINAL_JOURNAL_SHA256 = "74f12ec40b29bfb15aba77e6fded0431466d30028bd3a5eaa907586048150309"
RECEIPT_NAME = "range-b-bootstrap-reconciliation.json"
PRICE_IN = Decimal("0.000003")
PRICE_OUT = Decimal("0.000015")


def _event_trace_sha(trace: list) -> str:
    raw = (
        json.dumps([event.model_dump(mode="json") for event in trace], sort_keys=True) + "\n"
    ).encode()
    return _sha256(raw)


async def audit(
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, _ = _approval_and_manifest(source_root, protocol_root, manifest_path, approval_path)
    journal_bytes = journal_path.read_bytes()
    if _sha256(journal_bytes) != FINAL_JOURNAL_SHA256:
        raise ValueError("v1 final journal bytes differ")
    rows = [json.loads(line) for line in journal_bytes.splitlines()]
    starts = [row for row in rows if row["type"] == "cell_started"]
    completed = [row for row in rows if row["type"] == "cell_completed"]
    cells = manifest["cells"]
    if (
        len(rows) != 13
        or len(starts) != 6
        or len(completed) != 6
        or [row["cell_id"] for row in starts] != [cell["cell_id"] for cell in cells[:6]]
        or [row["cell_id"] for row in completed] != [cell["cell_id"] for cell in cells[:6]]
        or any(row["order"] != order for order, row in enumerate(completed, 1))
    ):
        raise ValueError("v1 stopped cell order or count differs")
    receipt = json.loads((journal_path.parent / RECEIPT_NAME).read_text())
    if (
        receipt["reconciled_journal_sha256"] != FINAL_JOURNAL_SHA256
        or receipt["retry_count"] != 0
        or receipt["cell_ids"] != [cell["cell_id"] for cell in cells[4:6]]
        or receipt["unstarted_cells"] != [cell["cell_id"] for cell in cells[6:]]
    ):
        raise ValueError("v1 Range B reconciliation receipt differs")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    engine = create_async_engine(database_url)
    state = ComposeRangeRuntime(state_dir).state
    try:
        event_store = PostgresEventStore(engine)
        run_ids = {UUID(row["run_id"]) for row in completed}
        async with engine.connect() as connection:
            authoritative_run_ids = set(
                (await connection.execute(select(runs.c.run_id))).scalars().all()
            )
        if not run_ids <= authoritative_run_ids:
            raise ValueError("dedicated pilot event store is missing a journaled source run")
        total_cost = Decimal(0)
        total_events = 0
        results = []
        replay_run_ids: set[UUID] = set()
        for order, (cell, record) in enumerate(zip(cells[:6], completed, strict=True), 1):
            run_id = UUID(record["run_id"])
            trace = await event_store.read_run(run_id)
            trace_path = Path(record["trace_path"])
            if (
                not trace_path.is_file()
                or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
                or _event_trace_sha(trace) != record["trace_sha256"]
                or [event.sequence_number for event in trace] != list(range(1, len(trace) + 1))
                or any(event.run_id != run_id for event in trace)
            ):
                raise ValueError(f"cell {order} authoritative trace differs")
            run_starts = [event for event in trace if isinstance(event, RunStarted)]
            terminals = [event for event in trace if isinstance(event, RunCompleted)]
            ranges = [event for event in trace if isinstance(event, RangeStarted)]
            spec = _spec(protocol_root, manifest, cell)
            if (
                len(run_starts) != 1
                or run_starts[0].experiment_hash != _expected_run_hash(spec, cell["arm"])
                or len(terminals) != 1
                or len(ranges) != 1
                or str(ranges[0].build_id) != record["build_id"]
                or terminals[0].status != record["status"]
                or any(
                    isinstance(event, (ActionRequested, ModelCallStarted, ModelCallCompleted))
                    and event.sequence_number > terminals[0].sequence_number
                    for event in trace
                )
            ):
                raise ValueError(f"cell {order} run boundary differs")
            build = state.verify_build_integrity(ranges[0].build_id)
            if (
                str(build.pair_id) != record["pair_id"]
                or build.artifact_digests.get("fixture.json") != record["fixture_digest"]
                or build.spec.seed != cell["seed"]
                or build.spec.patched != (cell["variant"] == "patched")
            ):
                raise ValueError(f"cell {order} build or fixture differs")
            projection = project_controller_events(trace)
            if (
                projection.active_workers
                or projection.active_actions
                or projection.active_coverage
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError(f"cell {order} has an active controller reservation")
            requests = [event for event in trace if isinstance(event, ActionRequested)]
            bootstrap = [event for event in requests if event.source_phase == "bootstrap"]
            model_starts = [event for event in trace if isinstance(event, ModelCallStarted)]
            model_calls = [event for event in trace if isinstance(event, ModelCallCompleted)]
            if (
                len(bootstrap) != record["bootstrap_actions"]
                or len(model_starts) != record["model_call_starts"]
                or len(model_calls) != record["model_calls"]
                or sum(event.input_tokens for event in model_calls) != record["input_tokens"]
                or sum(event.output_tokens for event in model_calls) != record["output_tokens"]
                or any(
                    event.provider != "openrouter" or event.model != "moonshotai/kimi-k3"
                    for event in model_starts
                )
                or any(
                    (event.resolved_model_revision, event.resolved_upstream_provider)
                    != ("moonshotai/kimi-k3-20260715", "Moonshot AI")
                    for event in model_calls
                )
            ):
                raise ValueError(f"cell {order} model or bootstrap accounting differs")
            cost = (
                Decimal(record["input_tokens"]) * PRICE_IN
                + Decimal(record["output_tokens"]) * PRICE_OUT
            )
            if Decimal(str(record["estimated_cost_usd"])) != cost:
                raise ValueError(f"cell {order} collector cost differs")
            total_cost += cost
            submissions = [event.finding for event in trace if isinstance(event, FindingSubmitted)]
            validations = {
                event.result.finding_id: event.result
                for event in trace
                if isinstance(event, FindingValidated)
            }
            if set(validations) != {finding.finding_id for finding in submissions}:
                raise ValueError(f"cell {order} finding verdict linkage differs")
            replay_run_ids.update(
                verdict.replay_trace.replay_run_id
                for verdict in validations.values()
                if verdict.replay_trace is not None
            )
            if order <= 4:
                if (
                    record["status"] != "budget_exhausted"
                    or record["score_valid"] is not True
                    or len(
                        [
                            event
                            for event in trace
                            if isinstance(event, PrerequisiteBootstrapCompleted)
                        ]
                    )
                    != 1
                ):
                    raise ValueError(f"cell {order} valid source boundary differs")
                oracle = GroundTruthManifest.model_validate_json(
                    (
                        state_dir / "oracles" / ranges[0].build_id.hex / "ground_truth.json"
                    ).read_bytes()
                )
                evaluation = evaluate_run(
                    tuple(submissions),
                    tuple(validations[finding.finding_id] for finding in submissions),
                    oracle,
                    status="budget_exhausted",
                )
            else:
                if (
                    record["status"] != "environment_failed"
                    or record["score_valid"] is not False
                    or len(trace) != 550
                    or len(bootstrap) != 32
                    or model_starts
                    or model_calls
                    or submissions
                    or any(isinstance(event, PrerequisiteBootstrapCompleted) for event in trace)
                ):
                    raise ValueError(f"cell {order} invalid bootstrap boundary differs")
                if _identity_hash(trace) != receipt["runs"][order - 5]["event_identity_sha256"]:
                    raise ValueError(f"cell {order} reconciliation event IDs differ")
                evaluation = unscored_run("environment_failed")
            if evaluation.model_dump(mode="json") != record["evaluation"]:
                raise ValueError(f"cell {order} oracle score replay differs")
            total_events += len(trace)
            results.append(
                {
                    "cell_id": cell["cell_id"],
                    "run_id": str(run_id),
                    "event_count": len(trace),
                    "event_identity_sha256": _identity_hash(trace),
                    "trace_sha256": record["trace_sha256"],
                    "status": record["status"],
                    "score_valid": record["score_valid"],
                    "model_calls": len(model_calls),
                    "bootstrap_actions": len(bootstrap),
                    "estimated_cost_usd": float(cost),
                }
            )
        if total_cost != Decimal("1.874538"):
            raise ValueError("v1 cumulative estimated model-token cost differs")
        if (
            authoritative_run_ids - run_ids != replay_run_ids
            or run_ids & replay_run_ids
            or len(replay_run_ids) != 2
        ):
            raise ValueError("extra event-store runs are not exactly cited validator replays")
        replay_runs = []
        for replay_id in sorted(replay_run_ids, key=str):
            replay = await event_store.read_run(replay_id)
            if (
                len(replay) != 19
                or [event.sequence_number for event in replay] != list(range(1, 20))
                or any(event.run_id != replay_id for event in replay)
                or sum(isinstance(event, RunStarted) for event in replay) != 1
                or [event.status for event in replay if isinstance(event, RunCompleted)]
                != ["completed"]
                or any(
                    isinstance(event, (ModelCallStarted, ModelCallCompleted)) for event in replay
                )
            ):
                raise ValueError("cited validator replay event stream differs")
            replay_runs.append(
                {
                    "run_id": str(replay_id),
                    "event_count": len(replay),
                    "event_identity_sha256": _identity_hash(replay),
                }
            )
        return {
            "protocol": manifest["protocol"],
            "manifest_sha256": _sha256(manifest_path.read_bytes()),
            "final_journal_sha256": FINAL_JOURNAL_SHA256,
            "reconciliation_receipt_sha256": _sha256(
                (journal_path.parent / RECEIPT_NAME).read_bytes()
            ),
            "planned_cells": len(cells),
            "journaled_cells": len(completed),
            "score_valid_cells": sum(record["score_valid"] for record in completed),
            "score_invalid_cells": sum(not record["score_valid"] for record in completed),
            "unstarted_cell_ids": [cell["cell_id"] for cell in cells[6:]],
            "total_events": total_events,
            "validator_replay_runs": replay_runs,
            "authoritative_event_count_including_replays": total_events
            + sum(item["event_count"] for item in replay_runs),
            "estimated_model_token_cost_usd": float(total_cost),
            "infrastructure_gate": "failed",
            "stage_extraction_gate": "failed_in_frozen_v1_protocol",
            "cells": results,
        }
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        audit(
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "journaled_cells",
                    "total_events",
                    "estimated_model_token_cost_usd",
                    "infrastructure_gate",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
