"""Collect the frozen M6.5.2 pilot or sample without retrying any source prefix."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from uuid import UUID, uuid4

import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import evaluate_run
from offsecgym.experiment.reporting_context import (
    PairedReportingContextRunner,
    arm_order_for_spec,
    paired_experiment_hash,
)
from offsecgym.providers.openrouter import OpenRouterResponsesProvider
from offsecgym.research.m64_execute import Journal, _sha256, _utc_now, _write_trace
from offsecgym.research.m652_analysis import (
    ReportingArmRecord,
    ReportingPrefixRecord,
    analyze_paired_reporting,
    source_complete_proof_roots,
)
from offsecgym.research.m652_audit import audit_paired_branches, replay_branch_score
from offsecgym.research.m652_checkpoint import ProbeCheckpointStore
from offsecgym.research.m652_matrix import (
    CHECKPOINT_AFTER_CALLS,
    PILOT_PROTOCOL,
    PROBE_CONFIG,
    REPORTER_CONFIG,
    maximum_cell_token_cost,
    plan_m652,
    spec_for_seed,
)
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import ValidationContext
from offsecgym.schemas.events import (
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    ReporterFinished,
    ReportingPrefixRejected,
    RunCompleted,
    RunStarted,
    WorkerSpawned,
)
from offsecgym.schemas.specs import ExperimentSpec, ReporterBudget
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events
from offsecgym.storage.reporting_branch import ReportingBranchStore, prefix_sha256
from offsecgym.worldview import EventWorldState


def verify_manifest(root: Path, path: Path) -> dict[str, Any]:
    recorded = json.loads(path.read_text())
    expected = plan_m652(
        root,
        source_commit=recorded["source_commit"],
        pilot=recorded.get("protocol") == PILOT_PROTOCOL,
    )
    if recorded != json.loads(json.dumps(expected)):
        raise ValueError("M6.5.2 manifest does not rebuild from pinned inputs")
    for source_path, digest in recorded["source_files"].items():
        pinned = subprocess.run(
            ["git", "show", f"{recorded['source_commit']}:{source_path}"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        if _sha256(pinned) != digest:
            raise ValueError("M6.5.2 source file differs from pinned source commit")
    frozen_paths = (
        "src/offsecgym",
        "migrations/versions/0008_reporting_branches.py",
        "research_ops/m652_postcheck.py",
        PROBE_CONFIG,
        REPORTER_CONFIG,
    )
    changed = subprocess.run(
        ["git", "diff", "--quiet", recorded["source_commit"], "HEAD", "--", *frozen_paths],
        cwd=root,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", *frozen_paths],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    if changed.returncode or dirty.stdout:
        raise ValueError("M6.5.2 runtime source differs from its frozen source commit")
    return recorded


def spec_for_cell(root: Path, manifest: dict[str, Any], cell: dict[str, Any]):
    probe_path, reporter_path = root / PROBE_CONFIG, root / REPORTER_CONFIG
    if (
        _sha256(probe_path.read_bytes()) != manifest["probe_config"]["sha256"]
        or _sha256(reporter_path.read_bytes()) != manifest["reporter_config"]["sha256"]
    ):
        raise ValueError("M6.5.2 configuration differs from manifest")
    base = ExperimentSpec.model_validate(yaml.safe_load(probe_path.read_text()))
    reporter = ReporterBudget.model_validate(yaml.safe_load(reporter_path.read_text()))
    seed = cell["range_seed"]
    patched = cell["variant"] == "patched"
    if seed not in manifest["seed_set"] or cell["variant"] not in ("patched", "vulnerable"):
        raise ValueError("M6.5.2 cell is outside frozen seed/variant set")
    spec = spec_for_seed(base, seed, patched)
    if paired_experiment_hash(spec, reporter, CHECKPOINT_AFTER_CALLS) != cell[
        "experiment_sha256"
    ] or arm_order_for_spec(spec) != tuple(cell["arm_order"]):
        raise ValueError("M6.5.2 experiment or arm order differs from manifest")
    return spec, reporter


async def _close_coverage(events: PostgresEventStore, run_id: UUID) -> None:
    trace = await events.read_run(run_id)
    if sum(isinstance(item, RunCompleted) for item in trace) != 1:
        raise ValueError("M6.5.2 administrative closure requires a terminal source run")
    projection = project_controller_events(trace)
    world = EventWorldState(events)
    for claim_id, task_id in projection.active_coverage.items():
        await world.update_coverage(run_id, claim_id, task_id, "released")


def _cost(calls: list[ModelCallCompleted], price: dict[str, object]) -> float:
    return (
        sum(item.input_tokens for item in calls) * price["input_usd_per_million"]
        + sum(item.output_tokens for item in calls) * price["output_usd_per_million"]
    ) / 1_000_000


def verify_live_price(manifest: dict[str, Any], metadata: dict[str, object]) -> None:
    """Fail before a paid cell if the pinned Moonshot endpoint price drifts."""
    data = metadata.get("data")
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, list):
        raise ValueError("OpenRouter endpoint metadata has no endpoint list")
    expected = manifest["expected_selected_endpoint"]
    matching = [
        item
        for item in endpoints
        if isinstance(item, dict)
        and item.get("provider_name") == expected["upstream_provider"]
        and item.get("name") == f"{expected['upstream_provider']} | {expected['revision']}"
    ]
    if len(matching) != 1 or not isinstance(matching[0].get("pricing"), dict):
        raise ValueError("pinned Moonshot AI model endpoint is not uniquely available")
    pricing = matching[0]["pricing"]
    frozen = manifest["price_snapshot"]
    try:
        input_price = Decimal(str(pricing["prompt"])) * 1_000_000
        output_price = Decimal(str(pricing["completion"])) * 1_000_000
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError("OpenRouter endpoint pricing is not usable") from exc
    if input_price != Decimal(str(frozen["input_usd_per_million"])) or output_price != Decimal(
        str(frozen["output_usd_per_million"])
    ):
        raise ValueError("OpenRouter endpoint price differs from frozen snapshot")


def fetch_and_verify_live_price(manifest: dict[str, Any]) -> None:
    url = manifest["price_snapshot"]["metadata_url"]
    with urlopen(url, timeout=15) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("OpenRouter endpoint metadata response is too large")
    metadata = json.loads(raw)
    if not isinstance(metadata, dict):
        raise ValueError("OpenRouter endpoint metadata is not an object")
    verify_live_price(manifest, metadata)


def _arm_record(
    arm: str,
    source_sha: str,
    evaluation,
    trace,
    prefix_count: int,
    *,
    patched: bool,
) -> ReportingArmRecord:
    events = trace[prefix_count:]
    calls = [item for item in events if isinstance(item, ModelCallCompleted)]
    new_findings = [item for item in events if isinstance(item, FindingSubmitted)]
    return ReportingArmRecord(
        arm=arm,
        source_trace_sha256=source_sha,
        score_valid=evaluation.score_valid,
        status=evaluation.status,
        validated_roots=(
            tuple(str(root) for root in evaluation.matched_root_cause_ids)
            if evaluation.score_valid
            else ()
        ),
        patched_false_findings=len(new_findings) if patched else 0,
        input_tokens=sum(item.input_tokens for item in calls),
        output_tokens=sum(item.output_tokens for item in calls),
        model_calls=len(calls),
        preflight_failures=sum(
            isinstance(item, ReporterFinished) and item.reason_code == "reporter_preflight"
            for item in events
        ),
    )


async def _collect_cell(
    root: Path,
    manifest: dict[str, Any],
    cell: dict[str, Any],
    journal: Journal,
    state_dir: Path,
    events: PostgresEventStore,
    runtime: ComposeRangeRuntime,
    provider: OpenRouterResponsesProvider,
) -> dict[str, Any]:
    spec, reporter_budget = spec_for_cell(root, manifest, cell)
    runner = PairedReportingContextRunner(
        runtime,
        events,
        provider,
        reporter_budget,
        checkpoint_after_calls=CHECKPOINT_AFTER_CALLS,
    )
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
    started = time.monotonic()
    outcome = await runner.run(spec, run_id=run_id)
    await _close_coverage(events, outcome.run_id)
    trace = list(await events.read_run(outcome.run_id))
    if outcome.build_id is None or str(outcome.build_id) != cell["build_id"]:
        raise ValueError("M6.5.2 range build differs from pinned pair")
    pair = manifest["range_pairs"][str(cell["range_seed"])]
    built = runtime.state.load_build(outcome.build_id)
    fixture_path = runtime.state.build_dir(outcome.build_id) / "fixture.json"
    if (
        str(built.pair_id) != pair["pair_id"]
        or _sha256(fixture_path.read_bytes()) != pair["fixture_sha256"]
    ):
        raise ValueError("M6.5.2 range pair or fixture differs from manifest")
    starts = [item for item in trace if isinstance(item, RunStarted)]
    ranges = [item for item in trace if isinstance(item, RangeStarted)]
    endings = [item for item in trace if isinstance(item, RunCompleted)]
    bootstrap = [item for item in trace if isinstance(item, PrerequisiteBootstrapCompleted)]
    bootstrap_actions = [
        item
        for item in trace
        if isinstance(item, ActionRequested) and item.source_phase == "bootstrap"
    ]
    if (
        len(starts) != 1
        or starts[0].experiment_hash != cell["experiment_sha256"]
        or len(ranges) != 1
        or ranges[0].build_id != outcome.build_id
        or len(endings) != 1
        or len(bootstrap) != 1
        or bootstrap[0].action_count != 17
        or len(bootstrap_actions) != 17
        or any(isinstance(item, WorkerSpawned) for item in trace)
        or [item.sequence_number for item in trace] != list(range(1, len(trace) + 1))
    ):
        raise ValueError("M6.5.2 source lifecycle or bootstrap boundary differs")
    projection = project_controller_events(trace)
    if (
        projection.active_workers
        or projection.active_actions
        or projection.active_coverage
        or projection.model_reservations
        or any(hold.active for hold in projection.admission_holds.values())
    ):
        raise ValueError("M6.5.2 source controller reservations remain active")
    expected_endpoint = (
        manifest["expected_selected_endpoint"]["revision"],
        manifest["expected_selected_endpoint"]["upstream_provider"],
    )
    source_calls = [item for item in trace if isinstance(item, ModelCallCompleted)]
    if source_calls and {
        (item.resolved_model_revision, item.resolved_upstream_provider) for item in source_calls
    } != {expected_endpoint}:
        raise ValueError("M6.5.2 source selected endpoint drifted")
    validation_context = ValidationContext(
        run_id=outcome.run_id,
        range_instance_id=ranges[0].range_instance_id,
        range_generation=ranges[0].range_generation,
        build_id=outcome.build_id,
    )
    oracle = StateOracleStore(runtime.state).load_for_context(validation_context)
    source_findings = tuple(item.finding for item in trace if isinstance(item, FindingSubmitted))
    source_verdicts = tuple(item.result for item in trace if isinstance(item, FindingValidated))
    if outcome.evaluation.score_valid:
        replayed = evaluate_run(source_findings, source_verdicts, oracle, status=endings[0].status)
        if replayed != outcome.evaluation:
            raise ValueError("M6.5.2 source score does not replay")
    checkpoints = (
        await ProbeCheckpointStore(events, state_dir).load_latest(outcome.run_id)
        if runner.arm_outcomes.get(outcome.run_id)
        else None
    )
    prefix = trace[: checkpoints.source_sequence + 1] if checkpoints else trace
    source_sha = prefix_sha256(prefix)
    if checkpoints and any(
        isinstance(item, ActionRequested) for item in trace[checkpoints.source_sequence + 1 :]
    ):
        raise ValueError("M6.5.2 source issued an action after the reporting split")
    arm_outcomes = runner.arm_outcomes.get(outcome.run_id, ())
    branch_records: dict[str, dict[str, object]] = {}
    arm_rows: dict[str, ReportingArmRecord] = {}
    all_calls = list(source_calls)
    audit: dict[str, object] | None = None
    if arm_outcomes:
        branch_ids = {item.arm: item.branch_id for item in arm_outcomes}
        if set(branch_ids) != {"fresh", "continuation"}:
            raise ValueError("M6.5.2 does not have exactly two reporting arms")
        audit = await audit_paired_branches(
            events,
            state_dir,
            outcome.run_id,
            branch_ids,
            provider=provider,
            model=spec.model,
            expected_endpoint=expected_endpoint,
        )
        fixture = json.loads(fixture_path.read_text())
        proof_roots = (
            source_complete_proof_roots(prefix, state_dir, oracle, fixture)
            if outcome.evaluation.score_valid and cell["variant"] == "vulnerable"
            else ()
        )
        for arm_outcome in arm_outcomes:
            branch = ReportingBranchStore(events, arm_outcome.branch_id, outcome.run_id)
            branch_trace = await branch.read_run(outcome.run_id)
            if await replay_branch_score(branch, oracle) != arm_outcome.evaluation:
                raise ValueError("M6.5.2 branch score does not replay")
            branch_calls = [
                item for item in branch_trace[len(prefix) :] if isinstance(item, ModelCallCompleted)
            ]
            all_calls.extend(branch_calls)
            arm_rows[arm_outcome.arm] = _arm_record(
                arm_outcome.arm,
                source_sha,
                arm_outcome.evaluation,
                branch_trace,
                len(prefix),
                patched=cell["variant"] == "patched",
            )
            path = journal.path.parent / "traces" / f"{cell['cell_id']}-{arm_outcome.arm}.json"
            branch_records[arm_outcome.arm] = {
                "branch_id": str(arm_outcome.branch_id),
                "trace_path": str(path),
                "trace_sha256": _write_trace(path, branch_trace),
                "evaluation": arm_outcome.evaluation.model_dump(mode="json"),
                "reporter_reason": arm_outcome.reporter_reason,
            }
    else:
        proof_roots = ()
        for arm in ("fresh", "continuation"):
            arm_rows[arm] = ReportingArmRecord(
                arm=arm,
                source_trace_sha256=source_sha,
                score_valid=False,
                status="not_run_prefix_rejected",
            )
    row = ReportingPrefixRecord(
        seed=cell["range_seed"],
        variant=cell["variant"],
        source_trace_sha256=source_sha,
        source_score_valid=outcome.evaluation.score_valid,
        source_validated_roots=(
            tuple(str(item) for item in outcome.evaluation.matched_root_cause_ids)
            if outcome.evaluation.score_valid
            else ()
        ),
        complete_proof_roots=proof_roots,
        fresh=arm_rows["fresh"],
        continuation=arm_rows["continuation"],
    )
    source_path = journal.path.parent / "traces" / f"{cell['cell_id']}-source.json"
    price = manifest["price_snapshot"]
    estimated_cost = _cost(all_calls, price)
    return {
        "type": "cell_completed",
        "cell_id": cell["cell_id"],
        "order": cell["order"],
        "at": _utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "build_id": str(outcome.build_id),
        "source_run_id": str(outcome.run_id),
        "source_status": outcome.evaluation.status,
        "source_evaluation": outcome.evaluation.model_dump(mode="json"),
        "source_trace_path": str(source_path),
        "source_trace_sha256": _write_trace(source_path, trace),
        "reporting_prefix_sha256": source_sha,
        "reporting_prefix_rejections": [
            item.reason_code for item in trace if isinstance(item, ReportingPrefixRejected)
        ],
        "branches": branch_records,
        "audit": audit,
        "analysis_record": row.model_dump(mode="json"),
        "model_calls": len(all_calls),
        "input_tokens": sum(item.input_tokens for item in all_calls),
        "output_tokens": sum(item.output_tokens for item in all_calls),
        "estimated_cost_usd": round(estimated_cost, 8),
    }


async def execute(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    state_dir: Path,
    *,
    max_estimated_usd: float,
    max_cells: int | None = None,
) -> dict[str, object]:
    manifest = verify_manifest(root, manifest_path)
    if (
        not math.isfinite(max_estimated_usd)
        or max_estimated_usd <= 0
        or abs(max_estimated_usd - manifest["cumulative_estimated_cost_stop_usd"]) > 1e-9
    ):
        raise ValueError("M6.5.2 requested cost differs from the frozen ceiling")
    if max_cells is not None and max_cells < 0:
        raise ValueError("max_cells cannot be negative")
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("OPENROUTER_API_KEY and OFFSECGYM_DATABASE_URL are required")
    expected_endpoint = (
        manifest["expected_selected_endpoint"]["revision"],
        manifest["expected_selected_endpoint"]["upstream_provider"],
    )
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
        cells = sorted(manifest["cells"], key=lambda item: item["order"])
        if [item["order"] for item in cells] != list(range(1, len(cells) + 1)):
            raise ValueError("M6.5.2 cell order is not consecutive")
        by_id = {item["cell_id"]: item for item in cells}
        if set(journal.completed) - set(by_id):
            raise ValueError("journal contains an unplanned M6.5.2 cell")
        for cell_id, record in journal.completed.items():
            source_path = Path(record["source_trace_path"])
            if (
                record["order"] != by_id[cell_id]["order"]
                or not source_path.is_file()
                or _sha256(source_path.read_bytes()) != record["source_trace_sha256"]
            ):
                raise ValueError("completed M6.5.2 source trace is not verified")
            for arm_record in record["branches"].values():
                path = Path(arm_record["trace_path"])
                if not path.is_file() or _sha256(path.read_bytes()) != arm_record["trace_sha256"]:
                    raise ValueError("completed M6.5.2 branch trace is not verified")
        spent = sum(record["estimated_cost_usd"] for record in journal.completed.values())
        new_cells = 0
        for cell in cells:
            if cell["cell_id"] in journal.completed:
                continue
            if max_cells is not None and new_cells >= max_cells:
                break
            spec, reporter_budget = spec_for_cell(root, manifest, cell)
            reserve = maximum_cell_token_cost(spec, reporter_budget)
            if round(reserve, 6) != manifest["per_cell_cost_ceiling_usd"]:
                raise ValueError("M6.5.2 reserved cost differs from price snapshot")
            if spent + reserve > max_estimated_usd + 1e-9:
                raise ValueError("insufficient approved USD for the next paired prefix")
            await asyncio.to_thread(fetch_and_verify_live_price, manifest)
            record = await _collect_cell(
                root, manifest, cell, journal, runtime.state.root, events, runtime, provider
            )
            journal.append(record)
            journal.completed[cell["cell_id"]] = record
            spent += record["estimated_cost_usd"]
            new_cells += 1
            if spent > max_estimated_usd:
                raise ValueError("M6.5.2 cumulative estimated USD ceiling exceeded")
            if record["reporting_prefix_rejections"] not in ([], ["probe_status_not_eligible"]):
                raise ValueError("M6.5.2 prefix integrity failed and was retained without retry")
            if not record["source_evaluation"]["score_valid"]:
                raise ValueError("M6.5.2 source is score-invalid and retained without retry")
            if any(
                not branch["evaluation"]["score_valid"] for branch in record["branches"].values()
            ):
                raise ValueError("M6.5.2 reporting arm is score-invalid and retained")
            if manifest["protocol"] == PILOT_PROTOCOL and (
                len(record["branches"]) != 2
                or not all(
                    item["first_request_bytes_reconstructed"] and item["model_calls"] >= 1
                    for item in record["audit"]["arms"].values()
                )
            ):
                raise ValueError("M6.5.2 non-sample feasibility pilot failed")
        complete = len(journal.completed) == len(cells)
        result: dict[str, object] = {
            "completed_cells": len(journal.completed),
            "planned_live_cells": len(cells),
            "estimated_cost_usd": round(spent, 6),
            "complete": complete,
        }
        if complete:
            output = journal.path.parent / "analysis.json"
            if manifest["protocol"] == PILOT_PROTOCOL:
                analysis = {
                    "protocol": PILOT_PROTOCOL,
                    "status": "non_sample_feasibility_only",
                    "cells": [
                        {
                            "cell_id": item["cell_id"],
                            "audit": journal.completed[item["cell_id"]]["audit"],
                            "source_status": journal.completed[item["cell_id"]]["source_status"],
                        }
                        for item in cells
                    ],
                    "estimated_cost_usd": round(spent, 6),
                }
            else:
                rows = tuple(
                    ReportingPrefixRecord.model_validate(
                        journal.completed[item["cell_id"]]["analysis_record"]
                    )
                    for item in cells
                )
                analysis = analyze_paired_reporting(rows, protocol=manifest["protocol"])
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
    print(
        json.dumps(
            asyncio.run(
                execute(
                    args.repository_root,
                    args.manifest,
                    args.journal,
                    args.state_dir,
                    max_estimated_usd=args.max_estimated_usd,
                    max_cells=args.max_cells,
                )
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
