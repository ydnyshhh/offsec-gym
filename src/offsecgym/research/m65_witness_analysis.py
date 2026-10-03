"""Predeclared descriptive analysis for the separate M6.5 recovery assay."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from offsecgym.research.m64_stage_ledger import ROOT_KINDS


def analyze_witness_matrix(
    manifest: dict[str, Any], completed: list[dict[str, Any]]
) -> dict[str, Any]:
    cells = {item["cell_id"]: item for item in manifest["cells"]}
    if len(completed) != len(cells) or {item["cell_id"] for item in completed} != set(cells):
        raise ValueError("analysis requires exactly one completed record per planned cell")
    invalid: list[dict[str, Any]] = []
    rows = []
    by_root: dict[str, Counter[str]] = defaultdict(Counter)
    for record in sorted(completed, key=lambda item: item["order"]):
        cell = cells[record["cell_id"]]
        conversion = record.get("conversion")
        observation = record["observation"]
        if (
            record["order"] != cell["order"]
            or observation["experiment_sha256"] != cell["experiment_sha256"]
            or conversion is None
            or conversion.get("run_id") != observation["run_id"]
        ):
            raise ValueError("journal cell differs from frozen Study B manifest or trace")
        score_valid = bool(observation["evaluation"]["score_valid"])
        reporter_valid = conversion.get("reporter_status") != "provider_failed"
        if not score_valid or not reporter_valid:
            invalid.append(
                {
                    "cell_id": cell["cell_id"],
                    "variant": cell["variant"],
                    "run_status": observation["evaluation"]["status"],
                    "reporter_status": conversion.get("reporter_status"),
                }
            )
        rows.append(
            {
                "seed": cell["range_seed"],
                "variant": cell["variant"],
                "score_valid": score_valid,
                "reporter_valid": reporter_valid,
                "original_validated_roots": conversion.get("original_validated_roots"),
                "incremental_reporter_roots": conversion.get("incremental_reporter_roots"),
                "original_false_findings": conversion.get("original_false_findings"),
                "reporter_false_findings": conversion.get("reporter_false_findings"),
                "reporter_duplicate_validated_submissions": conversion.get(
                    "reporter_duplicate_validated_submissions"
                ),
                "probe_tokens": (
                    observation["input_tokens"]
                    + observation["output_tokens"]
                    - conversion.get("reporter_input_tokens", 0)
                    - conversion.get("reporter_output_tokens", 0)
                ),
                "reporter_tokens": (
                    conversion.get("reporter_input_tokens", 0)
                    + conversion.get("reporter_output_tokens", 0)
                ),
            }
        )
        if not score_valid or not reporter_valid or cell["variant"] != "vulnerable":
            continue
        if len(conversion["rows"]) != len(ROOT_KINDS):
            raise ValueError("prospective ledger omitted a configured root")
        for root in conversion["rows"]:
            if not root["applicable"]:
                raise ValueError("vulnerable root was marked inapplicable")
            counter = by_root[root["root"]]
            counter["opportunities"] += 1
            for stage in (
                "ready",
                "admitted",
                "executed",
                "trace_proof",
                "submitted_original",
                "submitted_reporter",
                "validated_original",
                "validated_reporter",
                "incremental_reporter_root",
            ):
                counter[stage] += int(root[stage])
    valid_vulnerable = [
        row
        for row in rows
        if row["variant"] == "vulnerable" and row["score_valid"] and row["reporter_valid"]
    ]
    valid_patched = [
        row
        for row in rows
        if row["variant"] == "patched" and row["score_valid"] and row["reporter_valid"]
    ]
    return {
        "protocol": manifest["protocol"],
        "method": (
            "descriptive read-only recovery with extra reporter compute; "
            "no equal-compute causal claim"
        ),
        "planned_pairs": len(manifest["seed_set"]),
        "completed_cells": len(completed),
        "invalid_cells": invalid,
        "valid_vulnerable_cells": len(valid_vulnerable),
        "valid_patched_cells": len(valid_patched),
        "vulnerable_original_validated_roots": sum(
            row["original_validated_roots"] for row in valid_vulnerable
        ),
        "vulnerable_incremental_reporter_roots": sum(
            row["incremental_reporter_roots"] for row in valid_vulnerable
        ),
        "patched_reporter_false_findings": sum(
            row["reporter_false_findings"] for row in valid_patched
        ),
        "patched_original_false_findings": sum(
            row["original_false_findings"] for row in valid_patched
        ),
        "root_stage_counts": {root: dict(by_root[root]) for root in ROOT_KINDS},
        "rows": rows,
    }
