"""Collect only the separately approved M6.5 monolithic control matrix."""

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

from offsecgym.evaluation import RunEvaluation
from offsecgym.experiment import BootstrappedMonolithicExperimentRunner
from offsecgym.experiment.scripted import experiment_hash
from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.providers.openrouter import OpenRouterResponsesProvider
from offsecgym.research.m64_analysis import M64Observation
from offsecgym.research.m64_execute import Journal, _sha256, _utc_now, _write_trace
from offsecgym.research.m65_monolithic_analysis import analyze_m65_monolithic
from offsecgym.research.m65_monolithic_matrix import _cell_spec, plan_m65_monolithic_matrix
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import (
    ActionRequested,
    ModelCallCompleted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events

INPUT_USD_PER_MILLION = 3.0
OUTPUT_USD_PER_MILLION = 15.0
MAX_PROTOCOL_ESTIMATED_USD = 138.0


def spec_for_cell(root: Path, manifest: dict[str, Any], cell: dict[str, Any]) -> ExperimentSpec:
    config_path = root / manifest["config"]["path"]
    if _sha256(config_path.read_bytes()) != manifest["config"]["sha256"]:
        raise ValueError("control config differs from pinned manifest")
    base = ExperimentSpec.model_validate(yaml.safe_load(config_path.read_text()))
    spec = _cell_spec(
        base,
        cell["range_seed"],
        cell["variant"] == "patched",
        cell["model_token_budget"],
    )
    if experiment_hash(spec) != cell["experiment_sha256"]:
        raise ValueError("control experiment hash differs from pinned manifest")
    return spec


def verify_manifest(
    root: Path, path: Path, *, require_source_history: bool = True
) -> dict[str, Any]:
    recorded = json.loads(path.read_text())
    rebuilt = json.loads(
        json.dumps(plan_m65_monolithic_matrix(root, source_commit=recorded["source_commit"]))
    )
    if recorded != rebuilt:
        raise ValueError("M6.5 control manifest no longer rebuilds from pinned inputs")
    source_paths = [
        "src/offsecgym",
        ":!src/offsecgym/research/m65_monolithic_execute.py",
        ":!src/offsecgym/research/m65_monolithic_analysis.py",
        ":!src/offsecgym/research/m65_monolithic_matrix.py",
        "experiments/configs",
        "examples",
    ]
    if require_source_history:
        changed = subprocess.run(
            ["git", "diff", "--quiet", recorded["source_commit"], "HEAD", "--", *source_paths],
            cwd=root,
            check=False,
        )
        if changed.returncode != 0:
            raise ValueError("runtime source differs from the M6.5 control source commit")
    dirty = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *source_paths], cwd=root, check=False
    )
    if dirty.returncode != 0:
        raise ValueError("runtime source has uncommitted changes")
    return recorded


def _historical_records(root: Path, manifest: dict[str, Any], journal_path: Path):
    historical_path = root / manifest["historical_manifest"]["path"]
    if _sha256(historical_path.read_bytes()) != manifest["historical_manifest"]["sha256"]:
        raise ValueError("frozen M6.4 reference manifest changed")
    historical = json.loads(historical_path.read_text())
    records = [json.loads(line) for line in journal_path.read_text().splitlines() if line.strip()]
    if not records or records[0].get("manifest_sha256") != _sha256(historical_path.read_bytes()):
        raise ValueError("historical journal does not match its frozen manifest")
    completed = [record for record in records if record["type"] == "cell_completed"]
    for record in completed:
        trace_path = Path(record["trace_path"])
        if not trace_path.is_file() or _sha256(trace_path.read_bytes()) != record["trace_sha256"]:
            raise ValueError("historical trace is missing or changed")
    return historical, completed


async def execute(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    historical_journal_path: Path,
    state_dir: Path,
    *,
    max_estimated_usd: float,
    max_cells: int | None = None,
) -> dict[str, Any]:
    """Fail closed on drift, interruption, unscored outcomes, or spend threshold."""
    manifest = verify_manifest(root, manifest_path)
    if (
        not math.isfinite(max_estimated_usd)
        or not 0 < max_estimated_usd <= MAX_PROTOCOL_ESTIMATED_USD
    ):
        raise ValueError("estimated USD limit is outside this protocol's reviewed bound")
    if max_cells is not None and max_cells < 0:
        raise ValueError("max_cells cannot be negative")
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("OPENROUTER_API_KEY and OFFSECGYM_DATABASE_URL are required")
    historical, old_records = _historical_records(root, manifest, historical_journal_path)
    ordered = sorted(manifest["cells"], key=lambda cell: cell["order"])
    if [cell["order"] for cell in ordered] != list(range(1, len(ordered) + 1)):
        raise ValueError("manifest orders must be consecutive")
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
    runner = BootstrappedMonolithicExperimentRunner(
        ComposeRangeRuntime(state_dir),
        events,
        OpenRouterResponsesProvider(os.environ["OPENROUTER_API_KEY"]),
    )
    try:
        by_id = {cell["cell_id"]: cell for cell in ordered}
        if set(journal.completed) - set(by_id):
            raise ValueError("journal contains a cell outside the control manifest")
        for cell_id, record in journal.completed.items():
            cell = by_id[cell_id]
            if record["order"] != cell["order"]:
                raise ValueError("journal order differs from control manifest")
            observation = M64Observation.model_validate(record["observation"])
            if observation.experiment_sha256 != cell["experiment_sha256"]:
                raise ValueError("journal experiment hash differs from control manifest")
            trace_path = Path(record["trace_path"])
            if (
                not trace_path.is_file()
                or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
            ):
                raise ValueError("completed control trace is missing or changed")
        spent = sum(record["estimated_cost_usd"] for record in journal.completed.values())
        new_cells = 0
        for cell in ordered:
            if cell["cell_id"] in journal.completed:
                continue
            if max_cells is not None and new_cells >= max_cells:
                break
            reserve = cell["model_token_budget"] * OUTPUT_USD_PER_MILLION / 1_000_000
            if spent + reserve > max_estimated_usd + 1e-9:
                raise ValueError("insufficient estimated USD capacity for next control cell")
            spec = spec_for_cell(root, manifest, cell)
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
            trace = await events.read_run(outcome.run_id)
            if outcome.build_id is None or str(outcome.build_id) != cell["build_id"]:
                raise ValueError("control build differs from pinned pair")
            if (
                not any(
                    isinstance(event, RunStarted)
                    and event.experiment_hash == cell["experiment_sha256"]
                    for event in trace
                )
                or not any(
                    isinstance(event, RangeStarted) and event.build_id == outcome.build_id
                    for event in trace
                )
                or not any(isinstance(event, RunCompleted) for event in trace)
            ):
                raise ValueError("control run lifecycle is incomplete")
            bootstrap = [
                event for event in trace if isinstance(event, PrerequisiteBootstrapCompleted)
            ]
            bootstrap_actions = [
                event
                for event in trace
                if isinstance(event, ActionRequested) and event.source_phase == "bootstrap"
            ]
            if (
                len(bootstrap) != 1
                or bootstrap[0].action_count != 17
                or len(bootstrap_actions) != 17
            ):
                raise ValueError("control bootstrap action boundary changed")
            projection = project_controller_events(trace)
            if (
                projection.active_workers
                or projection.active_actions
                or projection.active_coverage
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError("control reservations remain active")
            calls = [event for event in trace if isinstance(event, ModelCallCompleted)]
            endpoints = {
                (event.resolved_model_revision, event.resolved_upstream_provider) for event in calls
            }
            if calls and endpoints != {expected_endpoint}:
                raise ValueError("selected model endpoint changed")
            input_tokens = sum(event.input_tokens for event in calls)
            output_tokens = sum(event.output_tokens for event in calls)
            estimated_cost = (
                input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION
            ) / 1_000_000
            observation = M64Observation(
                cell_id=cell["cell_id"],
                experiment_sha256=cell["experiment_sha256"],
                run_id=outcome.run_id,
                evaluation=RunEvaluation.model_validate(outcome.evaluation),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                resolved_model_revision=expected_endpoint[0],
                upstream_provider=expected_endpoint[1],
            )
            trace_path = journal_path.parent / "traces" / f"{cell['cell_id']}.json"
            trace_sha256 = _write_trace(trace_path, trace)
            record = {
                "type": "cell_completed",
                "cell_id": cell["cell_id"],
                "order": cell["order"],
                "at": _utc_now(),
                "duration_seconds": round(time.monotonic() - started_at, 3),
                "build_id": str(outcome.build_id),
                "estimated_cost_usd": round(estimated_cost, 8),
                "trace_path": str(trace_path),
                "trace_sha256": trace_sha256,
                "observation": observation.model_dump(mode="json"),
                "orchestration": orchestration_metrics(trace).model_dump(mode="json"),
                "failure_reason": outcome.failure_reason,
            }
            journal.append(record)
            journal.completed[cell["cell_id"]] = record
            spent += estimated_cost
            new_cells += 1
            if spent > max_estimated_usd:
                raise ValueError("cumulative estimated USD limit exceeded")
            if not outcome.evaluation.score_valid:
                raise ValueError("unscored control run retained without retry; investigate")
        complete = len(journal.completed) == len(ordered)
        result = {
            "completed_cells": len(journal.completed),
            "planned_live_cells": len(ordered),
            "estimated_cost_usd": round(spent, 6),
            "complete": complete,
        }
        if complete:
            analysis = analyze_m65_monolithic(
                manifest,
                [journal.completed[cell["cell_id"]] for cell in ordered],
                historical,
                old_records,
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
    parser.add_argument("--historical-journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--max-estimated-usd", type=float, required=True)
    parser.add_argument("--max-cells", type=int)
    args = parser.parse_args()
    result = asyncio.run(
        execute(
            args.repository_root,
            args.manifest,
            args.journal,
            args.historical_journal,
            args.state_dir,
            max_estimated_usd=args.max_estimated_usd,
            max_cells=args.max_cells,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
