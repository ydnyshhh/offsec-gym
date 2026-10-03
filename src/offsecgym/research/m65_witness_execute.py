"""Collect only the separately reviewed prospective M6.5 witness assay."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.reporter_recovery import (
    ReporterRecoveryRunner,
    recovery_experiment_hash,
)
from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.providers.openrouter import OpenRouterResponsesProvider
from offsecgym.research.m64_analysis import M64Observation
from offsecgym.research.m64_execute import Journal, _sha256, _utc_now, _write_trace
from offsecgym.research.m65_conversion_ledger import conversion_ledger
from offsecgym.research.m65_witness_analysis import analyze_witness_matrix
from offsecgym.research.m65_witness_matrix import (
    OUTPUT_USD_PER_MILLION,
    PILOT_PROTOCOL,
    PROBE_CONFIG,
    REPORTER_CONFIG,
    plan_witness_matrix,
    plan_witness_pilot,
    spec_for_seed,
)
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import ValidationContext
from offsecgym.schemas.events import (
    ActionRequested,
    ModelCallCompleted,
    ModelReservationRejected,
    ModelToolRejected,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    ReporterFinished,
    ReporterStarted,
    RunCompleted,
    RunStarted,
    WorkerSpawned,
)
from offsecgym.schemas.specs import ExperimentSpec, ReporterBudget
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events
from offsecgym.worldview import EventWorldState


def verify_manifest(root: Path, path: Path) -> dict[str, Any]:
    recorded = json.loads(path.read_text())
    planner = (
        plan_witness_pilot if recorded.get("protocol") == PILOT_PROTOCOL else plan_witness_matrix
    )
    rebuilt = planner(root, source_commit=recorded["source_commit"])
    if recorded != json.loads(json.dumps(rebuilt)):
        raise ValueError("M6.5.1 manifest no longer rebuilds from pinned inputs")
    paths = [
        "src/offsecgym",
        ":!src/offsecgym/research/m65_witness_matrix.py",
        ":!src/offsecgym/research/m65_witness_execute.py",
        ":!src/offsecgym/research/m65_witness_analysis.py",
        "experiments/configs",
        "examples",
    ]
    changed = subprocess.run(
        ["git", "diff", "--quiet", recorded["source_commit"], "HEAD", "--", *paths],
        cwd=root,
        check=False,
    )
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *paths], cwd=root, check=False)
    if changed.returncode != 0 or dirty.returncode != 0:
        raise ValueError("M6.5.1 runtime source differs from pinned commit")
    return recorded


def spec_for_cell(root: Path, manifest: dict[str, Any], cell: dict[str, Any]):
    if cell["range_seed"] not in manifest["seed_set"] or cell["variant"] not in {
        "vulnerable",
        "patched",
    }:
        raise ValueError("cell is outside frozen held-out pairs")
    probe_path, reporter_path = root / PROBE_CONFIG, root / REPORTER_CONFIG
    if (
        _sha256(probe_path.read_bytes()) != manifest["probe_config"]["sha256"]
        or _sha256(reporter_path.read_bytes()) != manifest["reporter_config"]["sha256"]
    ):
        raise ValueError("Study B config hash differs from pinned manifest")
    base = ExperimentSpec.model_validate(yaml.safe_load(probe_path.read_text()))
    reporter = ReporterBudget.model_validate(yaml.safe_load(reporter_path.read_text()))
    spec = spec_for_seed(base, cell["range_seed"], cell["variant"] == "patched")
    if recovery_experiment_hash(spec, reporter) != cell["experiment_sha256"]:
        raise ValueError("Study B experiment hash differs from pinned manifest")
    return spec, reporter


async def _close_terminal_monolithic_coverage(events, run_id) -> int:
    """Administrative release after RunCompleted; never changes probe actions or score."""
    trace = await events.read_run(run_id)
    if sum(isinstance(item, RunCompleted) for item in trace) != 1:
        raise ValueError("coverage closure requires a completed run")
    if any(isinstance(item, WorkerSpawned) for item in trace):
        raise ValueError("M6.5.1 probe must remain monolithic")
    projection = project_controller_events(trace)
    world = EventWorldState(events)
    for claim_id, task_id in projection.active_coverage.items():
        await world.update_coverage(run_id, claim_id, task_id, "released")
    return len(projection.active_coverage)


def terminal_cause(trace, failure_reason: str | None, model_call_cap: int) -> str:
    ending = next((e for e in reversed(trace) if isinstance(e, RunCompleted)), None)
    if ending is None:
        return "missing_terminal_event"
    if ending.status == "completed":
        return "completed"
    if ending.status == "agent_failed":
        rejected = [
            e for e in trace if isinstance(e, ModelToolRejected) and e.actor == "controller"
        ]
        if len(rejected) >= 3:
            return "repeated_tool_call_rejection"
        return failure_reason or "other_agent_failure"
    if ending.status == "budget_exhausted":
        if failure_reason:
            return failure_reason
        rejects = [e for e in trace if isinstance(e, ModelReservationRejected)]
        if rejects:
            return rejects[-1].reason_code
        calls = [e for e in trace if isinstance(e, ModelCallCompleted) and e.actor != "reporter"]
        return "model_call_limit" if len(calls) >= model_call_cap else "other_budget_limit"
    return failure_reason or ending.status


async def execute(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    state_dir: Path,
    *,
    max_estimated_usd: float,
    max_cells: int | None = None,
) -> dict[str, Any]:
    manifest = verify_manifest(root, manifest_path)
    if (
        not math.isfinite(max_estimated_usd)
        or max_estimated_usd <= 0
        or max_estimated_usd > manifest["cumulative_estimated_cost_stop_usd"]
    ):
        raise ValueError("Study B cost limit exceeds the reviewable protocol ceiling")
    if max_cells is not None and max_cells < 0:
        raise ValueError("max_cells cannot be negative")
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("OPENROUTER_API_KEY and OFFSECGYM_DATABASE_URL are required")
    endpoint = manifest["expected_selected_endpoint"]
    expected_endpoint = endpoint["revision"], endpoint["upstream_provider"]
    journal = Journal(
        journal_path,
        manifest_sha256=_sha256(manifest_path.read_bytes()),
        expected_endpoint=expected_endpoint,
        max_estimated_usd=max_estimated_usd,
    )
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    runtime = ComposeRangeRuntime(state_dir)
    provider = OpenRouterResponsesProvider(os.environ["OPENROUTER_API_KEY"])
    try:
        ordered = sorted(manifest["cells"], key=lambda item: item["order"])
        if [item["order"] for item in ordered] != list(range(1, len(ordered) + 1)):
            raise ValueError("Study B cell order is not consecutive")
        by_id = {item["cell_id"]: item for item in ordered}
        if set(journal.completed) - set(by_id):
            raise ValueError("journal contains a cell outside the Study B manifest")
        for cell_id, record in journal.completed.items():
            cell = by_id[cell_id]
            trace_path = Path(record["trace_path"])
            if (
                record["order"] != cell["order"]
                or record["observation"]["experiment_sha256"] != cell["experiment_sha256"]
                or not trace_path.is_file()
                or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
            ):
                raise ValueError("completed Study B cell cannot be verified")
        spent = sum(record["estimated_cost_usd"] for record in journal.completed.values())
        new_cells = 0
        for cell in ordered:
            if cell["cell_id"] in journal.completed:
                continue
            if max_cells is not None and new_cells >= max_cells:
                break
            reserve = (
                manifest["combined_model_budget"]["max_total_tokens"]
                * OUTPUT_USD_PER_MILLION
                / 1_000_000
            )
            if spent + reserve > max_estimated_usd + 1e-9:
                raise ValueError("insufficient approved estimated USD for next Study B cell")
            spec, reporter_budget = spec_for_cell(root, manifest, cell)
            runner = ReporterRecoveryRunner(runtime, events, provider, reporter_budget)
            journal.append(
                {
                    "type": "cell_started",
                    "cell_id": cell["cell_id"],
                    "order": cell["order"],
                    "at": _utc_now(),
                }
            )
            started_at = time.monotonic()
            outcome = await runner.run(spec)
            await _close_terminal_monolithic_coverage(events, outcome.run_id)
            trace = await events.read_run(outcome.run_id)
            if outcome.build_id is None or str(outcome.build_id) != cell["build_id"]:
                raise ValueError("Study B build differs from pinned pair")
            if (
                not any(
                    isinstance(item, RunStarted)
                    and item.experiment_hash == cell["experiment_sha256"]
                    for item in trace
                )
                or not any(
                    isinstance(item, RangeStarted) and item.build_id == outcome.build_id
                    for item in trace
                )
                or not any(isinstance(item, RunCompleted) for item in trace)
            ):
                raise ValueError("Study B run lifecycle is incomplete")
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
                raise ValueError("Study B bootstrap boundary changed")
            if any(isinstance(item, WorkerSpawned) for item in trace):
                raise ValueError("M6.5.1 worker event crossed monolithic probe boundary")
            projection = project_controller_events(trace)
            if (
                projection.active_workers
                or projection.active_actions
                or projection.active_coverage
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError("Study B controller reservations remain")
            calls = [item for item in trace if isinstance(item, ModelCallCompleted)]
            if calls and {
                (item.resolved_model_revision, item.resolved_upstream_provider) for item in calls
            } != {expected_endpoint}:
                raise ValueError("Study B selected endpoint drifted")
            input_tokens = sum(item.input_tokens for item in calls)
            output_tokens = sum(item.output_tokens for item in calls)
            estimated_cost = (
                input_tokens * manifest["price_snapshot"]["input_usd_per_million"]
                + output_tokens * manifest["price_snapshot"]["output_usd_per_million"]
            ) / 1_000_000
            conversion = None
            reporter_finished = [item for item in trace if isinstance(item, ReporterFinished)]
            if outcome.evaluation.score_valid and len(reporter_finished) == 1:
                ranges = [item for item in trace if isinstance(item, RangeStarted)]
                context = ValidationContext(
                    run_id=outcome.run_id,
                    range_instance_id=ranges[0].range_instance_id,
                    range_generation=ranges[0].range_generation,
                    build_id=outcome.build_id,
                )
                oracle = StateOracleStore(runtime.state).load_for_context(context)
                fixture_path = runtime.state.build_dir(outcome.build_id) / "fixture.json"
                fixture = json.loads(fixture_path.read_text())
                conversion = conversion_ledger(trace, state_dir, oracle, fixture)
                if not any(isinstance(item, ReporterStarted) for item in trace):
                    raise ValueError("Study B reporter start event is missing")
            observation = M64Observation(
                cell_id=cell["cell_id"],
                experiment_sha256=cell["experiment_sha256"],
                run_id=outcome.run_id,
                evaluation=outcome.evaluation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                resolved_model_revision=expected_endpoint[0],
                upstream_provider=expected_endpoint[1],
            )
            trace_path = journal_path.parent / "traces" / f"{cell['cell_id']}.json"
            trace_sha = _write_trace(trace_path, trace)
            record = {
                "type": "cell_completed",
                "cell_id": cell["cell_id"],
                "order": cell["order"],
                "at": _utc_now(),
                "duration_seconds": round(time.monotonic() - started_at, 3),
                "build_id": str(outcome.build_id),
                "estimated_cost_usd": round(estimated_cost, 8),
                "trace_path": str(trace_path),
                "trace_sha256": trace_sha,
                "observation": observation.model_dump(mode="json"),
                "conversion": conversion,
                "orchestration": orchestration_metrics(trace).model_dump(mode="json"),
                "failure_reason": outcome.failure_reason,
                "terminal_cause": terminal_cause(
                    trace, outcome.failure_reason, spec.budget.max_model_calls or 0
                ),
            }
            journal.append(record)
            journal.completed[cell["cell_id"]] = record
            spent += estimated_cost
            new_cells += 1
            if spent > max_estimated_usd:
                raise ValueError("Study B cumulative estimated USD limit exceeded")
            if not outcome.evaluation.score_valid or (
                reporter_finished and reporter_finished[0].status == "provider_failed"
            ):
                raise ValueError("unscored Study B stage retained without retry")
            if conversion is None:
                raise ValueError("Study B reporter lifecycle is absent from scored run")
        complete = len(journal.completed) == len(ordered)
        result = {
            "completed_cells": len(journal.completed),
            "planned_live_cells": len(ordered),
            "estimated_cost_usd": round(spent, 6),
            "complete": complete,
        }
        if complete:
            if manifest["protocol"] == PILOT_PROTOCOL:
                analysis = {
                    "protocol": PILOT_PROTOCOL,
                    "status": "non_sample_pilot_only",
                    "cells": [
                        {
                            "cell_id": item["cell_id"],
                            "variant": item["variant"],
                            "conversion": journal.completed[item["cell_id"]]["conversion"],
                        }
                        for item in ordered
                    ],
                    "estimated_cost_usd": round(spent, 6),
                }
            else:
                analysis = analyze_witness_matrix(
                    manifest, [journal.completed[item["cell_id"]] for item in ordered]
                )
            output = journal_path.parent / "analysis.json"
            output.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n")
            result["analysis_path"] = str(output)
        return result
    finally:
        await engine.dispose()
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--max-estimated-usd", type=float, required=True)
    parser.add_argument("--max-cells", type=int)
    args = parser.parse_args()
    result = asyncio.run(
        execute(
            args.repository_root,
            args.manifest,
            args.journal,
            args.state_dir,
            max_estimated_usd=args.max_estimated_usd,
            max_cells=args.max_cells,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
