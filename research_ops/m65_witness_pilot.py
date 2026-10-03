"""Two-cell, non-sample M6.5 witness pilot with a $6 worst-case token bound."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.reporter_recovery import ReporterRecoveryRunner, recovery_experiment_hash
from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.providers.openrouter import OpenRouterResponsesProvider
from offsecgym.research.m64_analysis import M64Observation
from offsecgym.research.m64_execute import Journal, _sha256, _utc_now, _write_trace
from offsecgym.research.m65_conversion_ledger import conversion_ledger
from offsecgym.research.m65_witness_matrix import (
    INPUT_USD_PER_MILLION,
    OUTPUT_USD_PER_MILLION,
    PROBE_CONFIG,
    REPORTER_CONFIG,
)
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.runtime.saas import SaasRangeCompiler
from offsecgym.schemas.domain import ValidationContext
from offsecgym.schemas.events import (
    ActionRequested,
    ModelCallCompleted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    ReporterFinished,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.specs import Budget, ExperimentSpec, RangeSpec
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events

PILOT_SEED = 2101
PILOT_MAX_ESTIMATED_USD = 6.0
PROTOCOL = "m65-witness-read-only-pilot-1"
SAMPLE_MANIFEST = "experiments/manifests/m65-witness-recovery-v1.json"


def pilot_spec(base: ExperimentSpec, patched: bool) -> ExperimentSpec:
    variant = "patched" if patched else "vulnerable"
    range_spec = RangeSpec.model_validate(
        {**base.range.model_dump(mode="json"), "seed": PILOT_SEED, "patched": patched}
    )
    return ExperimentSpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "name": f"m65_witness_pilot_{variant}",
            "seed": PILOT_SEED,
            "range": range_spec.model_dump(mode="json"),
        }
    )


def plan_pilot(root: Path) -> dict[str, Any]:
    sample_path = root / SAMPLE_MANIFEST
    sample = json.loads(sample_path.read_text())
    if PILOT_SEED in sample["seed_set"] or PILOT_SEED in range(1001, 1011):
        raise ValueError("pilot seed overlaps a completed or prospective sample")
    probe_path, reporter_path = root / PROBE_CONFIG, root / REPORTER_CONFIG
    if (
        _sha256(probe_path.read_bytes()) != sample["probe_config"]["sha256"]
        or _sha256(reporter_path.read_bytes()) != sample["reporter_config"]["sha256"]
        or any(
            _sha256((root / path).read_bytes()) != digest
            for path, digest in sample["source_files"].items()
        )
    ):
        raise ValueError("pilot differs from frozen sample source or config")
    base = ExperimentSpec.model_validate(yaml.safe_load(probe_path.read_text()))
    reporter = Budget.model_validate(yaml.safe_load(reporter_path.read_text()))
    with tempfile.TemporaryDirectory(prefix="offsecgym-m65-witness-pilot-plan-") as directory:
        compiler = SaasRangeCompiler(StateStore(Path(directory)))
        vulnerable = compiler.build(pilot_spec(base, False).range)
        patched_build = compiler.build(pilot_spec(base, True).range)
        fixture = (compiler.state.build_dir(vulnerable.build_id) / "fixture.json").read_bytes()
        if (
            vulnerable.pair_id != patched_build.pair_id
            or fixture
            != (compiler.state.build_dir(patched_build.build_id) / "fixture.json").read_bytes()
        ):
            raise ValueError("pilot paired builds differ")
    cells = []
    for order, is_patched in enumerate((False, True), start=1):
        variant = "patched" if is_patched else "vulnerable"
        spec = pilot_spec(base, is_patched)
        cells.append(
            {
                "cell_id": hashlib.sha256(f"{PROTOCOL}:{variant}".encode()).hexdigest()[:16],
                "order": order,
                "variant": variant,
                "range_seed": PILOT_SEED,
                "pair_id": str(vulnerable.pair_id),
                "build_id": str(patched_build.build_id if is_patched else vulnerable.build_id),
                "experiment_sha256": recovery_experiment_hash(spec, reporter),
            }
        )
    return {
        "protocol": PROTOCOL,
        "status": "preflight_not_authorized",
        "sample_manifest_sha256": _sha256(sample_path.read_bytes()),
        "source_commit": sample["source_commit"],
        "pilot_runner_sha256": _sha256(Path(__file__).read_bytes()),
        "seed": PILOT_SEED,
        "pair_id": str(vulnerable.pair_id),
        "fixture_sha256": _sha256(fixture),
        "model_request": sample["model_request"],
        "expected_selected_endpoint": sample["expected_selected_endpoint"],
        "price_snapshot": sample["price_snapshot"],
        "probe_budget": sample["probe_budget"],
        "reporter_budget": sample["reporter_budget"],
        "combined_model_budget": sample["combined_model_budget"],
        "cells": cells,
        "maximum_estimated_token_cost_usd": PILOT_MAX_ESTIMATED_USD,
        "requires_separate_paid_approval": True,
    }


def verify_pilot(root: Path, manifest_path: Path) -> dict[str, Any]:
    recorded = json.loads(manifest_path.read_text())
    if recorded != plan_pilot(root):
        raise ValueError("pilot manifest differs from frozen sample or runner")
    return recorded


async def run_pilot(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    state_dir: Path,
    *,
    max_estimated_usd: float,
) -> dict[str, Any]:
    manifest = verify_pilot(root, manifest_path)
    if not 0 < max_estimated_usd <= PILOT_MAX_ESTIMATED_USD:
        raise ValueError("pilot estimated cost limit exceeds the reviewed ceiling")
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("OPENROUTER_API_KEY and OFFSECGYM_DATABASE_URL are required")
    endpoint = manifest["expected_selected_endpoint"]
    expected = endpoint["revision"], endpoint["upstream_provider"]
    journal = Journal(
        journal_path,
        manifest_sha256=_sha256(manifest_path.read_bytes()),
        expected_endpoint=expected,
        max_estimated_usd=max_estimated_usd,
    )
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    runtime = ComposeRangeRuntime(state_dir)
    provider = OpenRouterResponsesProvider(os.environ["OPENROUTER_API_KEY"])
    base = ExperimentSpec.model_validate(yaml.safe_load((root / PROBE_CONFIG).read_text()))
    reporter_budget = Budget.model_validate(yaml.safe_load((root / REPORTER_CONFIG).read_text()))
    try:
        by_id = {item["cell_id"]: item for item in manifest["cells"]}
        if set(journal.completed) - set(by_id):
            raise ValueError("pilot journal contains an unknown cell")
        for cell_id, record in journal.completed.items():
            trace_path = Path(record["trace_path"])
            if (
                record["order"] != by_id[cell_id]["order"]
                or record["observation"]["experiment_sha256"] != by_id[cell_id]["experiment_sha256"]
                or not trace_path.is_file()
                or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
            ):
                raise ValueError("completed pilot cell cannot be verified")
        spent = sum(item["estimated_cost_usd"] for item in journal.completed.values())
        for cell in manifest["cells"]:
            if cell["cell_id"] in journal.completed:
                continue
            reserve = (
                manifest["combined_model_budget"]["max_total_tokens"]
                * OUTPUT_USD_PER_MILLION
                / 1_000_000
            )
            if spent + reserve > max_estimated_usd:
                raise ValueError("insufficient approved USD for next pilot cell")
            spec = pilot_spec(base, cell["variant"] == "patched")
            if recovery_experiment_hash(spec, reporter_budget) != cell["experiment_sha256"]:
                raise ValueError("pilot experiment hash drifted")
            journal.append(
                {
                    "type": "cell_started",
                    "cell_id": cell["cell_id"],
                    "order": cell["order"],
                    "at": _utc_now(),
                }
            )
            started = time.monotonic()
            outcome = await ReporterRecoveryRunner(runtime, events, provider, reporter_budget).run(
                spec
            )
            trace = await events.read_run(outcome.run_id)
            if outcome.build_id is None or str(outcome.build_id) != cell["build_id"]:
                raise ValueError("pilot build differs from pinned pair")
            if not any(
                isinstance(item, RunStarted) and item.experiment_hash == cell["experiment_sha256"]
                for item in trace
            ) or not any(isinstance(item, RunCompleted) for item in trace):
                raise ValueError("pilot run lifecycle is incomplete")
            ranges = [item for item in trace if isinstance(item, RangeStarted)]
            bootstrap = [item for item in trace if isinstance(item, PrerequisiteBootstrapCompleted)]
            bootstrap_actions = [
                item
                for item in trace
                if isinstance(item, ActionRequested) and item.source_phase == "bootstrap"
            ]
            if (
                len(ranges) != 1
                or len(bootstrap) != 1
                or bootstrap[0].action_count != 17
                or len(bootstrap_actions) != 17
            ):
                raise ValueError("pilot bootstrap boundary changed")
            projection = project_controller_events(trace)
            if (
                projection.active_workers
                or projection.active_actions
                or projection.active_coverage
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError("pilot controller reservations remain")
            calls = [item for item in trace if isinstance(item, ModelCallCompleted)]
            if calls and {
                (item.resolved_model_revision, item.resolved_upstream_provider) for item in calls
            } != {expected}:
                raise ValueError("pilot selected endpoint drifted")
            input_tokens = sum(item.input_tokens for item in calls)
            output_tokens = sum(item.output_tokens for item in calls)
            cost = (
                input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION
            ) / 1_000_000
            conversion = None
            reporter = [item for item in trace if isinstance(item, ReporterFinished)]
            if outcome.evaluation.score_valid and len(reporter) == 1:
                context = ValidationContext(
                    run_id=outcome.run_id,
                    range_instance_id=ranges[0].range_instance_id,
                    range_generation=ranges[0].range_generation,
                    build_id=outcome.build_id,
                )
                oracle = StateOracleStore(runtime.state).load_for_context(context)
                fixture = json.loads(
                    (runtime.state.build_dir(outcome.build_id) / "fixture.json").read_text()
                )
                conversion = conversion_ledger(trace, state_dir, oracle, fixture)
            observation = M64Observation(
                cell_id=cell["cell_id"],
                experiment_sha256=cell["experiment_sha256"],
                run_id=outcome.run_id,
                evaluation=outcome.evaluation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                resolved_model_revision=expected[0],
                upstream_provider=expected[1],
            )
            trace_path = journal_path.parent / "traces" / f"{cell['cell_id']}.json"
            trace_sha = _write_trace(trace_path, trace)
            record = {
                "type": "cell_completed",
                "cell_id": cell["cell_id"],
                "order": cell["order"],
                "at": _utc_now(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "build_id": str(outcome.build_id),
                "estimated_cost_usd": round(cost, 8),
                "trace_path": str(trace_path),
                "trace_sha256": trace_sha,
                "observation": observation.model_dump(mode="json"),
                "conversion": conversion,
                "orchestration": orchestration_metrics(trace).model_dump(mode="json"),
                "failure_reason": outcome.failure_reason,
            }
            journal.append(record)
            journal.completed[cell["cell_id"]] = record
            spent += cost
            if spent > max_estimated_usd:
                raise ValueError("pilot cumulative estimated cost exceeded")
            if (
                not outcome.evaluation.score_valid
                or not reporter
                or reporter[0].status == "provider_failed"
            ):
                raise ValueError("unscored pilot stage retained without retry")
        return {
            "completed_cells": len(journal.completed),
            "estimated_cost_usd": round(spent, 6),
            "complete": len(journal.completed) == 2,
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
    parser.add_argument("--max-estimated-usd", type=float, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                run_pilot(
                    args.repository_root,
                    args.manifest,
                    args.journal,
                    args.state_dir,
                    max_estimated_usd=args.max_estimated_usd,
                )
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
