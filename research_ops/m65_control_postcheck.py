"""Read-only post-collection audit of the frozen M6.5 control matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from offsecgym.evaluation import evaluate_run
from offsecgym.research.m65_monolithic_analysis import analyze_m65_monolithic
from offsecgym.research.m65_monolithic_execute import (
    INPUT_USD_PER_MILLION,
    OUTPUT_USD_PER_MILLION,
    _historical_records,
    verify_manifest,
)
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import ValidationContext
from offsecgym.schemas.events import (
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    RunCompleted,
    RunStarted,
    WorkerSpawned,
    parse_event,
)
from offsecgym.storage.projection import project_controller_events


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    historical_journal_path: Path,
    state_dir: Path,
    analysis_path: Path,
    database_summaries: Path | None = None,
) -> dict[str, object]:
    """Check every trace against its manifest, oracle, and frozen analysis."""
    manifest = verify_manifest(root, manifest_path)
    historical, old_records = _historical_records(root, manifest, historical_journal_path)
    rows = [json.loads(line) for line in journal_path.read_text().splitlines() if line.strip()]
    expected_endpoint = manifest["expected_selected_endpoint"]
    expected = expected_endpoint["revision"], expected_endpoint["upstream_provider"]
    header = rows[0]
    if (
        header["type"] != "batch_started"
        or header["manifest_sha256"] != _sha256(manifest_path.read_bytes())
        or (header["expected_model_revision"], header["expected_upstream_provider"]) != expected
        or header["max_estimated_usd"] != 138.0
    ):
        raise ValueError("journal header differs from the approved protocol")
    ordered = sorted(manifest["cells"], key=lambda cell: cell["order"])
    if len(ordered) != 100 or len(rows) != 201:
        raise ValueError("the 100-cell journal is incomplete")
    if any(
        rows[2 * index - 1]["type"] != "cell_started"
        or rows[2 * index]["type"] != "cell_completed"
        or rows[2 * index - 1]["cell_id"] != cell["cell_id"]
        or rows[2 * index]["cell_id"] != cell["cell_id"]
        or rows[2 * index]["order"] != index
        for index, cell in enumerate(ordered, start=1)
    ):
        raise ValueError("journal starts/completions differ from frozen order")

    old_bootstrap = {
        (cell["range_seed"], cell["variant"]): record["orchestration"]["bootstrap_snapshot_hash"]
        for cell in historical["cells"]
        if cell["policy_feasible"]
        for record in old_records
        if record["cell_id"] == cell["cell_id"]
    }
    oracle_store = StateOracleStore(StateStore(state_dir))
    database_rows = {}
    if database_summaries is not None:
        for line in database_summaries.read_text().splitlines():
            run_id, count, first, last, digest = line.split("|")
            database_rows[run_id] = (int(count), int(first), int(last), digest)
    statuses: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    run_ids = set()
    event_ids = set()
    completions = []
    spent = 0.0
    for index, cell in enumerate(ordered, start=1):
        record = rows[2 * index]
        completions.append(record)
        raw = Path(record["trace_path"]).read_bytes()
        if _sha256(raw) != record["trace_sha256"]:
            raise ValueError(f"trace hash differs for cell {index}")
        trace = [parse_event(item) for item in json.loads(raw)]
        if [item.sequence_number for item in trace] != list(range(1, len(trace) + 1)):
            raise ValueError(f"event sequence is not contiguous for cell {index}")
        observation = record["observation"]
        run_id = observation["run_id"]
        if run_id in run_ids or any(str(item.run_id) != run_id for item in trace):
            raise ValueError(f"run identity differs for cell {index}")
        run_ids.add(run_id)
        ids = {item.event_id for item in trace}
        if len(ids) != len(trace) or ids & event_ids:
            raise ValueError(f"event ID reused for cell {index}")
        event_ids.update(ids)
        if database_summaries is not None:
            identity_stream = ",".join(
                f"{item.sequence_number}:{item.event_id}:{item.type}" for item in trace
            )
            expected_db = (
                len(trace),
                1,
                len(trace),
                hashlib.md5(identity_stream.encode()).hexdigest(),
            )
            if database_rows.get(run_id) != expected_db:
                raise ValueError(f"PostgreSQL event identity differs for cell {index}")
        starts = [item for item in trace if isinstance(item, RunStarted)]
        ranges = [item for item in trace if isinstance(item, RangeStarted)]
        endings = [item for item in trace if isinstance(item, RunCompleted)]
        if len(starts) != 1 or len(ranges) != 1 or len(endings) != 1:
            raise ValueError(f"run lifecycle differs for cell {index}")
        if (
            starts[0].experiment_hash != cell["experiment_sha256"]
            or observation["experiment_sha256"] != cell["experiment_sha256"]
            or str(ranges[0].build_id) != cell["build_id"]
            or record["build_id"] != cell["build_id"]
            or endings[0].status != observation["evaluation"]["status"]
        ):
            raise ValueError(f"run, build, experiment, or status differs for cell {index}")
        if any(isinstance(item, WorkerSpawned) for item in trace):
            raise ValueError(f"worker event found in monolithic cell {index}")
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
            or bootstrap[0].snapshot_hash != record["orchestration"]["bootstrap_snapshot_hash"]
            or bootstrap[0].snapshot_hash != old_bootstrap[(cell["range_seed"], cell["variant"])]
        ):
            raise ValueError(f"bootstrap differs from matched M6.4 range for cell {index}")
        projection = project_controller_events(trace)
        if (
            projection.active_workers
            or projection.active_actions
            or projection.active_coverage
            or projection.model_reservations
            or any(hold.active for hold in projection.admission_holds.values())
        ):
            raise ValueError(f"controller reservations remain for cell {index}")
        calls = [item for item in trace if isinstance(item, ModelCallCompleted)]
        if (
            not calls
            or len(calls) > 20
            or {(item.resolved_model_revision, item.resolved_upstream_provider) for item in calls}
            != {expected}
        ):
            raise ValueError(f"model call count or endpoint differs for cell {index}")
        input_tokens = sum(item.input_tokens for item in calls)
        output_tokens = sum(item.output_tokens for item in calls)
        cost = (
            input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION
        ) / 1_000_000
        if (
            observation["input_tokens"] != input_tokens
            or observation["output_tokens"] != output_tokens
            or abs(record["estimated_cost_usd"] - round(cost, 8)) > 1e-8
        ):
            raise ValueError(f"model usage or cost differs for cell {index}")
        findings = tuple(item.finding for item in trace if isinstance(item, FindingSubmitted))
        validations = tuple(item.result for item in trace if isinstance(item, FindingValidated))
        context = ValidationContext(
            run_id=ranges[0].run_id,
            range_instance_id=ranges[0].range_instance_id,
            range_generation=ranges[0].range_generation,
            build_id=ranges[0].build_id,
        )
        oracle = oracle_store.load_for_context(context)
        active_roots = {item.root_cause_id for item in oracle.properties if item.active}
        if len(active_roots) != (5 if cell["variant"] == "vulnerable" else 0):
            raise ValueError(f"oracle root count differs for cell {index}")
        score = evaluate_run(findings, validations, oracle, status=endings[0].status)
        if score.model_dump(mode="json") != observation["evaluation"]:
            raise ValueError(f"independent score replay differs for cell {index}")
        spent += record["estimated_cost_usd"]
        statuses[score.status] += 1
        totals["events"] += len(trace)
        totals["model_calls"] += len(calls)
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["candidates"] += score.candidate_count
        totals["validated_submissions"] += score.validated_count
        totals["distinct_validated_roots"] += score.true_positives
        totals["false_findings"] += score.false_positives
        totals["duplicate_validated_roots"] += score.duplicates
        if trace[-1].sequence_number > endings[0].sequence_number:
            totals["postrun_bookkeeping_cells"] += 1
    if spent > header["max_estimated_usd"]:
        raise ValueError("approved estimated-cost threshold exceeded")
    analysis = analyze_m65_monolithic(manifest, completions, historical, old_records)
    expected_analysis = json.dumps(analysis, indent=2, sort_keys=True) + "\n"
    if analysis_path.read_text() != expected_analysis:
        raise ValueError("predeclared analysis does not replay byte for byte")
    return {
        "protocol": manifest["protocol"],
        "manifest_sha256": _sha256(manifest_path.read_bytes()),
        "journal_sha256": _sha256(journal_path.read_bytes()),
        "analysis_sha256": _sha256(analysis_path.read_bytes()),
        "completed_cells": len(completions),
        "score_valid_cells": sum(statuses.values()),
        "distinct_run_ids": len(run_ids),
        "estimated_token_cost_usd": round(spent, 6),
        "statuses": dict(sorted(statuses.items())),
        "totals": dict(sorted(totals.items())),
        "selected_endpoint": list(expected),
        "database_event_identity_matched_runs": len(run_ids) if database_summaries else None,
        "historical_comparison": "matched historical, not concurrent randomized",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--historical-journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--database-summaries", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            audit(
                args.repository_root,
                args.manifest,
                args.journal,
                args.historical_journal,
                args.state_dir,
                args.analysis,
                args.database_summaries,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
