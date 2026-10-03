"""Predeclared descriptive M6.5.1 recovery analysis on paired held-out seeds."""

from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict
from typing import Any

from offsecgym.research.m65_conversion_ledger import ROOTS

BOOTSTRAP_REPLICATES = 10_000


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _seed_bootstrap(rows: list[dict[str, Any]], protocol: str) -> list[float] | None:
    if not rows or sum(row["recoverable_missed_roots"] for row in rows) == 0:
        return None
    seed = int.from_bytes(hashlib.sha256(protocol.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    estimates = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        denominator = sum(row["recoverable_missed_roots"] for row in sample)
        if denominator:
            estimates.append(sum(row["reporter_recovered_roots"] for row in sample) / denominator)
    if not estimates:
        return None
    estimates.sort()
    return [
        estimates[int(0.025 * (len(estimates) - 1))],
        estimates[int(0.975 * (len(estimates) - 1))],
    ]


def analyze_witness_matrix(
    manifest: dict[str, Any], completed: list[dict[str, Any]]
) -> dict[str, Any]:
    cells = {x["cell_id"]: x for x in manifest["cells"]}
    if (
        len(cells) != 20
        or len(completed) != 20
        or {x["cell_id"] for x in completed} != set(cells)
        or len({x["cell_id"] for x in completed}) != 20
    ):
        raise ValueError("analysis requires each of 20 frozen cells exactly once")
    by_root: dict[str, Counter[str]] = defaultdict(Counter)
    refund: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    patched_classes: Counter[str] = Counter()
    terminal_causes: Counter[str] = Counter()
    invalid = []
    rows = []
    vulnerable_for_bootstrap = []
    for record in sorted(completed, key=lambda x: x["order"]):
        cell = cells[record["cell_id"]]
        observation = record["observation"]
        conversion = record.get("conversion")
        if (
            record["order"] != cell["order"]
            or record["build_id"] != cell["build_id"]
            or observation["experiment_sha256"] != cell["experiment_sha256"]
            or conversion is None
            or conversion["run_id"] != observation["run_id"]
        ):
            raise ValueError("journal does not match frozen cell or trace ledger")
        valid = (
            bool(observation["evaluation"]["score_valid"])
            and bool(conversion.get("score_valid"))
            and conversion["reporter_status"] != "provider_failed"
        )
        if not valid:
            invalid.append(
                {
                    "cell_id": cell["cell_id"],
                    "seed": cell["range_seed"],
                    "variant": cell["variant"],
                    "probe_status": observation["evaluation"]["status"],
                    "reporter_status": conversion["reporter_status"],
                }
            )
        terminal_causes[record.get("terminal_cause", "unreported")] += 1
        row = {
            "seed": cell["range_seed"],
            "variant": cell["variant"],
            "valid": valid,
            "probe_status": observation["evaluation"]["status"],
            "reporter_status": conversion["reporter_status"],
            "recoverable_missed_roots": 0,
            "reporter_recovered_roots": 0,
        }
        rows.append(row)
        if not valid:
            continue
        for key in (
            "integrated_candidates",
            "reporter_candidates",
            "integrated_rejected",
            "reporter_rejected",
            "integrated_validated_submissions",
            "reporter_validated_submissions",
            "integrated_distinct_roots",
            "reporter_distinct_roots",
            "incremental_reporter_roots",
            "combined_distinct_roots",
            "integrated_duplicate_validated_submissions",
            "reporter_duplicate_validated_submissions",
            "integrated_same_root_distinct_witness_submissions",
            "reporter_same_root_distinct_witness_submissions",
            "probe_agent_http_actions",
            "probe_agent_http_attempts",
            "probe_exact_repeated_http_actions",
            "probe_model_calls",
            "probe_input_tokens",
            "probe_output_tokens",
            "reporter_model_calls",
            "reporter_input_tokens",
            "reporter_output_tokens",
            "reporter_retrieval_calls",
        ):
            totals[key] += conversion[key]
        if cell["variant"] == "patched":
            totals["patched_integrated_false_findings"] += conversion["integrated_rejected"]
            totals["patched_reporter_false_findings"] += conversion["reporter_rejected"]
            patched_classes.update(conversion["patched_reporter_false_classes"])
            for slug, count in conversion["patched_reporter_false_by_root"].items():
                if slug in ROOTS:
                    by_root[slug]["patched_reporter_false_findings"] += count
                else:
                    totals["patched_reporter_false_unmapped_or_ambiguous"] += count
            continue
        if len(conversion["rows"]) != len(ROOTS) or {x["root"] for x in conversion["rows"]} != set(
            ROOTS
        ):
            raise ValueError("prospective ledger omitted a configured vulnerable root")
        for root in conversion["rows"]:
            if not root["applicable"]:
                raise ValueError("vulnerable root was marked inapplicable")
            c = by_root[root["root"]]
            c["vulnerable_opportunities"] += 1
            for stage in (
                "attempted_relevant_action",
                "executed_relevant_action",
                "successful_relevant_action",
                "complete_trace_proof",
                "integrated_matching_submission",
                "integrated_evidence_valid_submission",
                "integrated_validated",
                "reporter_matching_submission",
                "reporter_evidence_valid_submission",
                "reporter_validated",
                "recoverable_missed_root",
                "reporter_recovered_root",
                "matching_submission_incomplete_or_wrong_proof",
            ):
                c[stage] += int(root[stage])
            row["recoverable_missed_roots"] += int(root["recoverable_missed_root"])
            row["reporter_recovered_roots"] += int(root["reporter_recovered_root"])
            if root["root"] == "MEMBER-REFUND":
                for stage, value in root["refund"].items():
                    if isinstance(value, bool):
                        refund[stage] += int(value)
                    elif isinstance(value, int):
                        refund[stage] += value
                    elif stage == "citation_error_codes":
                        for code in value:
                            refund[f"citation_error:{code}"] += 1
        vulnerable_for_bootstrap.append(row)
    recoverable = sum(row["recoverable_missed_roots"] for row in vulnerable_for_bootstrap)
    recovered = sum(row["reporter_recovered_roots"] for row in vulnerable_for_bootstrap)
    complete_proofs = sum(c["complete_trace_proof"] for c in by_root.values())
    integrated_from_proof = sum(c["integrated_validated"] for c in by_root.values())
    root_rows = []
    for root in ROOTS:
        c = by_root[root]
        root_rows.append(
            {
                "root": root,
                "patched_reporter_false_findings": c["patched_reporter_false_findings"],
                **dict(c),
                "integrated_proof_to_finding": _ratio(
                    c["integrated_validated"], c["complete_trace_proof"]
                ),
                "reporter_recovery_conversion": _ratio(
                    c["reporter_recovered_root"], c["recoverable_missed_root"]
                ),
            }
        )
    markdown = [
        "| Root | Opportunities | Complete proof | Integrated validated | "
        "Recoverable missed | Reporter recovered |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for c in root_rows:
        markdown.append(
            f"| {c['root']} | {c.get('vulnerable_opportunities', 0)} | "
            f"{c.get('complete_trace_proof', 0)} | {c.get('integrated_validated', 0)} | "
            f"{c.get('recoverable_missed_root', 0)} | {c.get('reporter_recovered_root', 0)} |"
        )
    reporter_tokens = totals["reporter_input_tokens"] + totals["reporter_output_tokens"]
    return {
        "protocol": manifest["protocol"],
        "method": "paired-seed descriptive recovery with extra inference",
        "sample_size": {
            "pairs": len(manifest["seed_set"]),
            "cells": len(completed),
            "valid_vulnerable": len(vulnerable_for_bootstrap),
            "valid_patched": sum(r["valid"] and r["variant"] == "patched" for r in rows),
        },
        "invalid_cells": invalid,
        "primary": {
            "reporter_recovered_distinct_roots": recovered,
            "recoverable_missed_roots": recoverable,
            "recovery_fraction": _ratio(recovered, recoverable),
            "paired_seed_bootstrap_95pct": _seed_bootstrap(
                vulnerable_for_bootstrap, manifest["protocol"]
            ),
            "resampling_unit": "seed pair; 10000 deterministic resamples",
        },
        "integrated_validated_given_complete_proof": _ratio(integrated_from_proof, complete_proofs),
        "root_rows": root_rows,
        "refund_transition": dict(refund),
        "overall": {
            **dict(totals),
            "terminal_causes": dict(terminal_causes),
            "patched_reporter_false_classes": dict(patched_classes),
            "roots_recovered_per_100k_reporter_tokens": _ratio(
                recovered * 100_000, reporter_tokens
            ),
            "total_model_tokens": totals["probe_input_tokens"]
            + totals["probe_output_tokens"]
            + reporter_tokens,
        },
        "rows": rows,
        "markdown_root_table": "\n".join(markdown),
        "interpretation_limit": (
            "Recovery shows reportability with extra inference on frozen traces; "
            "it does not compare against an equally funded integrated continuation."
        ),
    }
