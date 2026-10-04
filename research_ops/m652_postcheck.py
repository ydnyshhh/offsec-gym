"""Read-only authoritative replay of the frozen M6.5.2 journal and analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import evaluate_run, unscored_run
from offsecgym.providers.openrouter import OpenRouterResponsesProvider
from offsecgym.research.m64_execute import _sha256
from offsecgym.research.m652_analysis import (
    ReportingArmRecord,
    ReportingPrefixRecord,
    analyze_paired_reporting,
    source_complete_proof_roots,
)
from offsecgym.research.m652_audit import audit_paired_branches, replay_branch_score
from offsecgym.research.m652_checkpoint import ProbeCheckpointStore
from offsecgym.research.m652_execute import _arm_record, _cost, spec_for_cell, verify_manifest
from offsecgym.research.m652_matrix import PILOT_PROTOCOL
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
    parse_event,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.reporting_branch import ReportingBranchStore, prefix_sha256


def _file_trace(path: Path, digest: str):
    raw = path.read_bytes()
    if _sha256(raw) != digest:
        raise ValueError(f"M6.5.2 trace hash differs: {path}")
    return [parse_event(item) for item in json.loads(raw)]


def _same_trace(left, right) -> bool:
    return [item.model_dump(mode="json") for item in left] == [
        item.model_dump(mode="json") for item in right
    ]


async def postcheck(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    state_dir: Path,
    analysis_path: Path,
    database_url: str,
) -> dict[str, object]:
    manifest = verify_manifest(root, manifest_path)
    records = [json.loads(line) for line in journal_path.read_text().splitlines() if line.strip()]
    if not records or records[0].get("manifest_sha256") != _sha256(manifest_path.read_bytes()):
        raise ValueError("M6.5.2 journal is not bound to the frozen manifest")
    cells = sorted(manifest["cells"], key=lambda item: item["order"])
    completions = [item for item in records[1:] if item.get("type") == "cell_completed"]
    starts = [item for item in records[1:] if item.get("type") == "cell_started"]
    if (
        len(completions) != len(cells)
        or len(starts) != len(cells)
        or len(records) != 1 + 2 * len(cells)
        or [item["cell_id"] for item in completions] != [item["cell_id"] for item in cells]
        or [item["cell_id"] for item in starts] != [item["cell_id"] for item in cells]
    ):
        raise ValueError("M6.5.2 journal has incomplete or reordered cells")
    state = StateStore(state_dir)
    oracle_store = StateOracleStore(state)
    provider = OpenRouterResponsesProvider("read-only-postcheck")
    engine = create_async_engine(database_url)
    events = PostgresEventStore(engine)
    seen_runs: set[UUID] = set()
    seen_events: set[UUID] = set()
    rows: list[ReportingPrefixRecord] = []
    totals: Counter[str] = Counter()
    spent = 0.0
    endpoint = (
        manifest["expected_selected_endpoint"]["revision"],
        manifest["expected_selected_endpoint"]["upstream_provider"],
    )
    try:
        for cell, start_record, record in zip(cells, starts, completions, strict=True):
            spec, _ = spec_for_cell(root, manifest, cell)
            run_id = UUID(record["source_run_id"])
            if start_record["run_id"] != str(run_id):
                raise ValueError("M6.5.2 journal start and completion bind different runs")
            if run_id in seen_runs:
                raise ValueError("M6.5.2 journal reused a source run ID")
            seen_runs.add(run_id)
            pair = manifest["range_pairs"][str(cell["range_seed"])]
            build = state.load_build(UUID(cell["build_id"]))
            fixture_path = state.build_dir(build.build_id) / "fixture.json"
            if (
                str(build.pair_id) != pair["pair_id"]
                or _sha256(fixture_path.read_bytes()) != pair["fixture_sha256"]
            ):
                raise ValueError("M6.5.2 range pair or fixture differs from manifest")
            source = list(await events.read_run(run_id))
            saved = _file_trace(Path(record["source_trace_path"]), record["source_trace_sha256"])
            if not _same_trace(source, saved):
                raise ValueError("M6.5.2 source trace differs from authoritative PostgreSQL")
            if [item.sequence_number for item in source] != list(range(1, len(source) + 1)):
                raise ValueError("M6.5.2 source events are not contiguous")
            for item in source:
                if item.event_id in seen_events:
                    raise ValueError("M6.5.2 event ID reused across source runs")
                seen_events.add(item.event_id)
            starts_in_trace = [item for item in source if isinstance(item, RunStarted)]
            ranges = [item for item in source if isinstance(item, RangeStarted)]
            endings = [item for item in source if isinstance(item, RunCompleted)]
            bootstrap = [
                item for item in source if isinstance(item, PrerequisiteBootstrapCompleted)
            ]
            if (
                len(starts_in_trace) != 1
                or starts_in_trace[0].experiment_hash != cell["experiment_sha256"]
                or len(ranges) != 1
                or str(ranges[0].build_id) != cell["build_id"]
                or len(endings) != 1
                or len(bootstrap) != 1
                or bootstrap[0].action_count != 17
                or sum(
                    isinstance(item, ActionRequested) and item.source_phase == "bootstrap"
                    for item in source
                )
                != 17
            ):
                raise ValueError("M6.5.2 source provenance or bootstrap differs")
            context = ValidationContext(
                run_id=run_id,
                range_instance_id=ranges[0].range_instance_id,
                range_generation=ranges[0].range_generation,
                build_id=ranges[0].build_id,
            )
            oracle = oracle_store.load_for_context(context)
            source_findings = tuple(
                item.finding for item in source if isinstance(item, FindingSubmitted)
            )
            source_verdicts = tuple(
                item.result for item in source if isinstance(item, FindingValidated)
            )
            if endings[0].status in {"completed", "budget_exhausted", "agent_failed"}:
                source_score = evaluate_run(
                    source_findings, source_verdicts, oracle, status=endings[0].status
                )
            else:
                source_score = unscored_run(
                    endings[0].status,
                    len(source_findings),
                    validated_count=sum(item.status == "validated" for item in source_verdicts),
                    inconclusive=sum(item.status == "inconclusive" for item in source_verdicts),
                )
            if source_score.model_dump(mode="json") != record["source_evaluation"]:
                raise ValueError("M6.5.2 source score does not replay")
            source_calls = [item for item in source if isinstance(item, ModelCallCompleted)]
            all_calls = list(source_calls)
            arms: dict[str, ReportingArmRecord] = {}
            branches = record["branches"]
            if branches:
                checkpoint = await ProbeCheckpointStore(events, state.root).load_latest(run_id)
                prefix = source[: checkpoint.source_sequence + 1]
                if any(
                    isinstance(item, ActionRequested)
                    for item in source[checkpoint.source_sequence + 1 :]
                ):
                    raise ValueError("M6.5.2 source action occurred after reporting split")
                ids = {arm: UUID(item["branch_id"]) for arm, item in branches.items()}
                replayed_audit = await audit_paired_branches(
                    events,
                    state.root,
                    run_id,
                    ids,
                    provider=provider,
                    model=spec.model,
                    expected_endpoint=endpoint,
                )
                if replayed_audit != record["audit"]:
                    raise ValueError("M6.5.2 branch boundary audit differs")
                fixture = json.loads(fixture_path.read_text())
                proof_roots = (
                    source_complete_proof_roots(prefix, state.root, oracle, fixture)
                    if cell["variant"] == "vulnerable"
                    else ()
                )
                for arm, branch_record in branches.items():
                    branch = ReportingBranchStore(events, ids[arm], run_id)
                    branch_trace = await branch.read_run(run_id)
                    saved_branch = _file_trace(
                        Path(branch_record["trace_path"]), branch_record["trace_sha256"]
                    )
                    if not _same_trace(branch_trace, saved_branch):
                        raise ValueError("M6.5.2 branch trace differs from PostgreSQL")
                    totals["branch_events"] += len(branch_trace) - len(prefix)
                    for item in branch_trace[len(prefix) :]:
                        if item.event_id in seen_events:
                            raise ValueError("M6.5.2 branch event ID reused")
                        seen_events.add(item.event_id)
                    score = await replay_branch_score(branch, oracle)
                    if score.model_dump(mode="json") != branch_record["evaluation"]:
                        raise ValueError("M6.5.2 branch score does not replay")
                    all_calls.extend(
                        item
                        for item in branch_trace[len(prefix) :]
                        if isinstance(item, ModelCallCompleted)
                    )
                    arms[arm] = _arm_record(
                        arm,
                        prefix_sha256(prefix),
                        score,
                        branch_trace,
                        len(prefix),
                        patched=cell["variant"] == "patched",
                    )
            else:
                prefix = source
                proof_roots = ()
                for arm in ("fresh", "continuation"):
                    arms[arm] = ReportingArmRecord(
                        arm=arm,
                        source_trace_sha256=prefix_sha256(prefix),
                        score_valid=False,
                        status="not_run_prefix_rejected",
                    )
            if source_calls and {
                (item.resolved_model_revision, item.resolved_upstream_provider)
                for item in source_calls
            } != {endpoint}:
                raise ValueError("M6.5.2 source selected endpoint differs")
            row = ReportingPrefixRecord(
                seed=cell["range_seed"],
                variant=cell["variant"],
                source_trace_sha256=prefix_sha256(prefix),
                source_score_valid=source_score.score_valid,
                source_validated_roots=(
                    tuple(str(item) for item in source_score.matched_root_cause_ids)
                    if source_score.score_valid
                    else ()
                ),
                complete_proof_roots=proof_roots,
                fresh=arms["fresh"],
                continuation=arms["continuation"],
            )
            if row.model_dump(mode="json") != record["analysis_record"]:
                raise ValueError("M6.5.2 source-only eligibility or arm record differs")
            rows.append(row)
            price = manifest["price_snapshot"]
            cost = _cost(all_calls, price)
            if abs(record["estimated_cost_usd"] - round(cost, 8)) > 1e-8:
                raise ValueError("M6.5.2 usage or cost differs")
            spent += record["estimated_cost_usd"]
            totals["source_events"] += len(source)
            totals["model_calls"] += len(all_calls)
        if spent > manifest["cumulative_estimated_cost_stop_usd"]:
            raise ValueError("M6.5.2 journal exceeded its cost stop")
        if manifest["protocol"] == PILOT_PROTOCOL:
            expected_analysis = {
                "protocol": PILOT_PROTOCOL,
                "status": "non_sample_feasibility_only",
                "cells": [
                    {
                        "cell_id": item["cell_id"],
                        "audit": record["audit"],
                        "source_status": record["source_status"],
                    }
                    for item, record in zip(cells, completions, strict=True)
                ],
                "estimated_cost_usd": round(spent, 6),
            }
        else:
            expected_analysis = analyze_paired_reporting(tuple(rows), protocol=manifest["protocol"])
        if (
            analysis_path.read_text()
            != json.dumps(expected_analysis, indent=2, sort_keys=True) + "\n"
        ):
            raise ValueError("M6.5.2 analysis does not replay byte for byte")
    finally:
        await engine.dispose()
    return {
        "protocol": manifest["protocol"],
        "manifest_sha256": _sha256(manifest_path.read_bytes()),
        "journal_sha256": _sha256(journal_path.read_bytes()),
        "analysis_sha256": _sha256(analysis_path.read_bytes()),
        "completed_cells": len(completions),
        "distinct_source_runs": len(seen_runs),
        "distinct_event_ids": len(seen_events),
        "estimated_token_cost_usd": round(spent, 6),
        "selected_endpoint": list(endpoint),
        "totals": dict(sorted(totals.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    result = asyncio.run(
        postcheck(
            args.repository_root,
            args.manifest,
            args.journal,
            args.state_dir,
            args.analysis,
            args.database_url,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
