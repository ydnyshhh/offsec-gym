"""Offline import and paired summaries for retained model-run exports."""

from __future__ import annotations

import csv
import json
import os
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Literal

from pydantic import Field

from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.evaluation import RunEvaluation
from offsecgym.research.pilot.offline import OfflineRunRecord, analyze_run, empty_report
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import ModelCallStarted, parse_event
from offsecgym.schemas.ground_truth import GroundTruthManifest


class RetainedRunMetadata(StrictModel):
    provenance: Literal["model_run"] = "model_run"
    protocol_version: Literal["r0-a1-model-pilot-v1"] = "r0-a1-model-pilot-v1"
    task_id: str = Field(pattern=r"^t0-[0-9a-f]{64}$")
    seed: Literal[42, 43, 44]
    variant: Literal["vulnerable", "patched"]
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    visibility_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_version: str = Field(min_length=1)


def read_retained_run(prefix: Path) -> tuple[RetainedRunMetadata, OfflineRunRecord]:
    """Read a three-file export; never call Docker, gateway, or a model provider."""
    metadata = RetainedRunMetadata.model_validate_json(
        prefix.with_suffix(".metadata.json").read_text()
    )
    expected = make_task_manifest(
        "A1", task_mode="scoped_audit", seed=metadata.seed, variant=metadata.variant
    ).task.task_id
    if metadata.task_id != expected:
        raise ValueError("retained model-run task ID differs from frozen A1 cell")
    events = tuple(
        parse_event(json.loads(line))
        for line in prefix.with_suffix(".events.jsonl").read_text().splitlines()
        if line.strip()
    )
    if not any(isinstance(row, ModelCallStarted) for row in events):
        raise ValueError("retained trace contains no model calls; reference runs are excluded")
    if any(
        row.provider != metadata.provider or row.model != metadata.model_id
        for row in events
        if isinstance(row, ModelCallStarted)
    ):
        raise ValueError("model-call identity differs from retained run metadata")
    evaluation = RunEvaluation.model_validate_json(
        prefix.with_suffix(".evaluation.json").read_text()
    )
    oracle_path = prefix.with_suffix(".oracle.json")
    oracle = (
        GroundTruthManifest.model_validate_json(oracle_path.read_text())
        if oracle_path.exists()
        else None
    )
    expected_root = None
    if oracle is not None:
        active_roots = {row.root_cause_id for row in oracle.properties if row.active}
        if metadata.variant == "vulnerable" and len(active_roots) != 1:
            raise ValueError("vulnerable A1 oracle must contain exactly one active root")
        if metadata.variant == "patched" and active_roots:
            raise ValueError("patched A1 oracle must contain no active roots")
        expected_root = next(iter(active_roots), None)
    record = analyze_run(
        events, evaluation=evaluation, oracle=oracle, expected_root_cause_id=expected_root
    )
    if record.integrity_issues:
        raise ValueError("retained trace has event integrity issues")
    return metadata, record


def paired_report(
    rows: tuple[tuple[RetainedRunMetadata, OfflineRunRecord], ...],
) -> dict[str, object]:
    if not rows:
        return empty_report()
    identities = [(meta.provider, meta.model_id, meta.seed, meta.variant) for meta, _ in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate assigned model cell")
    budgets = {meta.budget_hash for meta, _ in rows}
    protocols = {meta.protocol_version for meta, _ in rows}
    tool_schemas = {meta.tool_schema_hash for meta, _ in rows}
    sources = {meta.source_commit for meta, _ in rows}
    visibility = {meta.visibility_contract_hash for meta, _ in rows}
    evaluations = {meta.evaluation_version for meta, _ in rows}
    if any(
        len(values) != 1
        for values in (budgets, protocols, tool_schemas, sources, visibility, evaluations)
    ):
        raise ValueError(
            "model comparisons require shared budget, source, visibility, and evaluation"
        )
    by_model: dict[tuple[str, str], list[tuple[RetainedRunMetadata, OfflineRunRecord]]] = {}
    for meta, record in rows:
        by_model.setdefault((meta.provider, meta.model_id), []).append((meta, record))
    models = []
    for (provider, model_id), entries in sorted(by_model.items()):
        models.append(
            {
                "provider": provider,
                "model_id": model_id,
                "run_count": len(entries),
                "score_valid_runs": sum(record.score_valid is True for _, record in entries),
                "vulnerable_root_successes": sum(
                    record.root_success is True
                    for meta, record in entries
                    if meta.variant == "vulnerable"
                ),
                "vulnerable_scored_runs": sum(
                    record.root_success is not None
                    for meta, record in entries
                    if meta.variant == "vulnerable"
                ),
                "patched_rejected_submissions": sum(
                    record.rejected_findings
                    for meta, record in entries
                    if meta.variant == "patched"
                ),
                "run_status_counts": dict(
                    sorted(Counter(record.run_status for _, record in entries).items())
                ),
                "mean_actions": mean(record.actions for _, record in entries),
                "median_actions": median(record.actions for _, record in entries),
                "paired_cells": [
                    {
                        "seed": meta.seed,
                        "variant": meta.variant,
                        "run_status": record.run_status,
                        "score_valid": record.score_valid,
                        "root_success": record.root_success,
                        "actions": record.actions,
                        "validated_findings": record.validated_findings,
                        "rejected_findings": record.rejected_findings,
                    }
                    for meta, record in sorted(
                        entries, key=lambda pair: (pair[0].seed, pair[0].variant)
                    )
                ],
            }
        )
    cell_sets = {
        (model["provider"], model["model_id"]): {
            (row["seed"], row["variant"]) for row in model["paired_cells"]
        }
        for model in models
    }
    shared_cells = set.intersection(*cell_sets.values()) if cell_sets else set()
    return {
        "analysis_version": "r0-offline-analysis-v1",
        "status": "analyzed_existing_unqualified",
        "actual_model_runs": len(rows),
        "development_pilot": True,
        "held_out_evaluation": False,
        "model_performance_claims": False,
        "score_replay_agreement_known_runs": sum(
            record.score_replay_agrees is True for _, record in rows
        ),
        "budget_hash": next(iter(budgets)),
        "models": models,
        "paired_seed_variant_cells": [list(cell) for cell in sorted(shared_cells)],
        "small_sample_warning": "Development pilot; no model ranking inference from three seeds.",
    }


def write_offline_reports(
    input_dir: Path, output_dir: Path, *, expected_budget_hash: str | None = None
) -> dict[str, object]:
    """Write exact run records only inside a private `.offsecgym` tree."""
    output = output_dir.resolve()
    if ".offsecgym" not in output.parts:
        raise ValueError("retained run analysis requires a private .offsecgym output directory")
    prefixes = sorted(
        path.with_name(path.name.removesuffix(".metadata.json"))
        for path in input_dir.glob("*.metadata.json")
    )
    rows = tuple(read_retained_run(prefix) for prefix in prefixes)
    if expected_budget_hash is not None and any(
        metadata.budget_hash != expected_budget_hash for metadata, _ in rows
    ):
        raise ValueError("retained model-run budget differs from frozen pilot protocol")
    summary = paired_report(rows)
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    (output / "pilot_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (output / "pilot_comparison.json").write_text(
        json.dumps(
            {
                "status": summary["status"],
                "paired_seed_variant_cells": summary.get("paired_seed_variant_cells", []),
                "models": summary["models"],
                "development_pilot": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with (output / "pilot_runs.jsonl").open("w") as stream:
        for metadata, record in rows:
            stream.write(
                json.dumps(
                    {
                        "metadata": metadata.model_dump(mode="json"),
                        "metrics": record.model_dump(mode="json"),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    with (output / "pilot_summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "provider",
                "model_id",
                "run_count",
                "score_valid_runs",
                "vulnerable_root_successes",
                "vulnerable_scored_runs",
                "patched_rejected_submissions",
                "mean_actions",
                "median_actions",
            ),
        )
        writer.writeheader()
        for model in summary["models"]:
            writer.writerow({name: model[name] for name in writer.fieldnames})
    with (output / "pilot_stages.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("provider", "model_id", "seed", "variant", "stage", "status")
        )
        writer.writeheader()
        for metadata, record in rows:
            for stage, status in sorted(record.stages.items()):
                writer.writerow(
                    {
                        "provider": metadata.provider,
                        "model_id": metadata.model_id,
                        "seed": metadata.seed,
                        "variant": metadata.variant,
                        "stage": stage,
                        "status": status,
                    }
                )
    for name in (
        "pilot_summary.json",
        "pilot_comparison.json",
        "pilot_runs.jsonl",
        "pilot_summary.csv",
        "pilot_stages.csv",
    ):
        (output / name).chmod(0o600)
    return summary
