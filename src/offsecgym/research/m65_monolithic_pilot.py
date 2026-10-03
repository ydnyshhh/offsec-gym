"""Two-cell, non-sample live preflight for the M6.5 monolithic control."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment import BootstrappedMonolithicExperimentRunner
from offsecgym.experiment.scripted import experiment_hash
from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.providers.openrouter import OpenRouterResponsesProvider
from offsecgym.research.m64_execute import Journal, _sha256, _utc_now, _write_trace
from offsecgym.research.m65_monolithic_execute import verify_manifest
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

PILOT_SEED = 1101
PILOT_TOKENS = 40000
PER_RUN_USD_CAP = 1.0
TOTAL_ESTIMATED_USD_CAP = 2.0
EXPECTED_BOOTSTRAP_SHA256 = "0e50d8ea223b31b19729e859054c8d4c3398e31aeec059ee4ecff3acbe8ba4a1"
EXPECTED_PAIR_ID = "7c46302a-1df7-5eed-9644-564cb1690f3b"


def pilot_spec(root: Path, manifest: dict[str, Any], *, patched: bool) -> ExperimentSpec:
    path = root / manifest["config"]["path"]
    if _sha256(path.read_bytes()) != manifest["config"]["sha256"]:
        raise ValueError("pilot config differs from frozen control config")
    base = ExperimentSpec.model_validate(yaml.safe_load(path.read_text()))
    return ExperimentSpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "name": f"m65_non_sample_pilot_1101_{'patched' if patched else 'vulnerable'}",
            "range": {
                **base.range.model_dump(mode="json"),
                "seed": PILOT_SEED,
                "patched": patched,
            },
            "budget": {
                **base.budget.model_dump(mode="json"),
                "max_total_tokens": PILOT_TOKENS,
                "max_cost_usd": PER_RUN_USD_CAP,
            },
            "model": {
                **base.model.model_dump(mode="json"),
                "input_usd_per_million_tokens": 3.0,
                "output_usd_per_million_tokens": 15.0,
            },
        }
    )


async def run_pilot(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict[str, Any]:
    manifest = verify_manifest(root, manifest_path)
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("OPENROUTER_API_KEY and OFFSECGYM_DATABASE_URL are required")
    endpoint = manifest["expected_selected_endpoint"]
    expected_endpoint = endpoint["revision"], endpoint["upstream_provider"]
    journal = Journal(
        journal_path,
        manifest_sha256=_sha256(manifest_path.read_bytes()),
        expected_endpoint=expected_endpoint,
        max_estimated_usd=TOTAL_ESTIMATED_USD_CAP,
    )
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    runner = BootstrappedMonolithicExperimentRunner(
        ComposeRangeRuntime(state_dir),
        events,
        OpenRouterResponsesProvider(os.environ["OPENROUTER_API_KEY"]),
    )
    cells = [(1, "vulnerable", False), (2, "patched", True)]
    try:
        if set(journal.completed) - {variant for _, variant, _ in cells}:
            raise ValueError("pilot journal contains an unknown cell")
        for order, variant, patched in cells:
            record = journal.completed.get(variant)
            if record is None:
                continue
            expected_hash = experiment_hash(pilot_spec(root, manifest, patched=patched))
            trace_path = Path(record["trace_path"])
            if (
                record["order"] != order
                or record["experiment_sha256"] != expected_hash
                or not trace_path.is_file()
                or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
                or not record["score_valid"]
                or record["bootstrap_snapshot_sha256"] != EXPECTED_BOOTSTRAP_SHA256
                or record["model_calls"] < 1
                or record["estimated_cost_usd"] > PER_RUN_USD_CAP
            ):
                raise ValueError("completed pilot record differs from its pinned run")
        for order, variant, patched in cells:
            if variant in journal.completed:
                continue
            spent = sum(record["estimated_cost_usd"] for record in journal.completed.values())
            if spent + PER_RUN_USD_CAP > TOTAL_ESTIMATED_USD_CAP + 1e-9:
                raise ValueError("pilot estimated spend cannot fund the next run")
            spec = pilot_spec(root, manifest, patched=patched)
            journal.append(
                {
                    "type": "cell_started",
                    "cell_id": variant,
                    "order": order,
                    "experiment_sha256": experiment_hash(spec),
                    "at": _utc_now(),
                }
            )
            started_at = time.monotonic()
            outcome = await runner.run(spec)
            trace = await events.read_run(outcome.run_id)
            if (
                not any(
                    isinstance(event, RunStarted) and event.experiment_hash == experiment_hash(spec)
                    for event in trace
                )
                or not any(isinstance(event, RangeStarted) for event in trace)
                or not any(isinstance(event, RunCompleted) for event in trace)
            ):
                raise ValueError("pilot lifecycle is incomplete")
            bootstrap = [
                event for event in trace if isinstance(event, PrerequisiteBootstrapCompleted)
            ]
            requests = [
                event
                for event in trace
                if isinstance(event, ActionRequested) and event.source_phase == "bootstrap"
            ]
            if (
                len(bootstrap) != 1
                or bootstrap[0].snapshot_hash != EXPECTED_BOOTSTRAP_SHA256
                or bootstrap[0].action_count != 17
                or len(requests) != 17
            ):
                raise ValueError("pilot bootstrap drifted from frozen worker pilot")
            if outcome.build_id is None:
                raise ValueError("pilot build is missing")
            build = runner.runtime.state.verify_build_integrity(outcome.build_id)
            if str(build.pair_id) != EXPECTED_PAIR_ID:
                raise ValueError("pilot pair differs from frozen worker pilot")
            projection = project_controller_events(trace)
            if (
                projection.active_workers
                or projection.active_actions
                or projection.active_coverage
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError("pilot controller reservations remain active")
            calls = [event for event in trace if isinstance(event, ModelCallCompleted)]
            if not calls:
                raise ValueError("pilot made no completed model call")
            endpoints = {
                (event.resolved_model_revision, event.resolved_upstream_provider) for event in calls
            }
            if calls and endpoints != {expected_endpoint}:
                raise ValueError("pilot selected endpoint drifted")
            input_tokens = sum(event.input_tokens for event in calls)
            output_tokens = sum(event.output_tokens for event in calls)
            estimated = (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000
            trace_path = journal_path.parent / "traces" / f"{variant}.json"
            trace_sha = _write_trace(trace_path, trace)
            record = {
                "type": "cell_completed",
                "cell_id": variant,
                "order": order,
                "at": _utc_now(),
                "run_id": str(outcome.run_id),
                "experiment_sha256": experiment_hash(spec),
                "build_id": str(outcome.build_id),
                "duration_seconds": round(time.monotonic() - started_at, 3),
                "status": outcome.evaluation.status,
                "score_valid": outcome.evaluation.score_valid,
                "evaluation": outcome.evaluation.model_dump(mode="json"),
                "model_calls": len(calls),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": round(estimated, 8),
                "bootstrap_snapshot_sha256": bootstrap[0].snapshot_hash,
                "trace_path": str(trace_path),
                "trace_sha256": trace_sha,
                "orchestration": orchestration_metrics(trace).model_dump(mode="json"),
                "failure_reason": outcome.failure_reason,
            }
            journal.append(record)
            journal.completed[variant] = record
            if estimated > PER_RUN_USD_CAP:
                raise ValueError("pilot run exceeded its estimated USD cap")
            if (
                sum(item["estimated_cost_usd"] for item in journal.completed.values())
                > TOTAL_ESTIMATED_USD_CAP
            ):
                raise ValueError("pilot cumulative estimated spend exceeded its cap")
            if not outcome.evaluation.score_valid:
                raise ValueError("pilot unscored run retained without retry; investigate")
        return {
            "completed": len(journal.completed),
            "planned": len(cells),
            "estimated_cost_usd": round(
                sum(item["estimated_cost_usd"] for item in journal.completed.values()), 6
            ),
        }
    finally:
        await engine.dispose()
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        run_pilot(args.repository_root, args.manifest, args.journal, args.state_dir)
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
