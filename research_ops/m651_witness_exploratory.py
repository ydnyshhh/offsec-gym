"""Post hoc reporter-call diagnostics, separate from the frozen primary analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def analyze(journal_path: Path, state_dir: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in journal_path.read_text().splitlines() if line]
    completed = [row for row in rows if row["type"] == "cell_completed"]
    if len(completed) != 20 or len({row["cell_id"] for row in completed}) != 20:
        raise ValueError("exploratory analysis requires the completed frozen 20-cell sample")

    terminal_reasons: Counter[str] = Counter()
    calls_per_cell: Counter[int] = Counter()
    retrievals_by_call: Counter[int] = Counter()
    submissions_by_call: Counter[int] = Counter()
    validated_by_call: Counter[int] = Counter()
    duplicate_roots_by_call: Counter[int] = Counter()
    rejected_by_call: Counter[int] = Counter()
    novel_roots_by_call: Counter[int] = Counter()
    rejected_reasons: Counter[str] = Counter()
    retrieved_exact_ids: set[tuple[str, str]] = set()
    cited_ids: set[tuple[str, str]] = set()
    first_novel = []
    reporter_tokens = 0

    for record in sorted(completed, key=lambda row: row["order"]):
        raw = Path(record["trace_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["trace_sha256"]:
            raise ValueError("trace changed after the frozen sample")
        trace = json.loads(raw)
        conversion = record["conversion"]
        run_id = record["observation"]["run_id"]
        reporter_start = next(item for item in trace if item["type"] == "reporter_started")
        reporter_finish = next(item for item in trace if item["type"] == "reporter_finished")
        reporter_calls = [
            item
            for item in trace
            if item["type"] == "model_call_completed" and item["actor"] == "reporter"
        ]
        call_number_by_event = {
            item["event_id"]: index for index, item in enumerate(reporter_calls, start=1)
        }
        call_number_by_id = {
            item["call_id"]: index for index, item in enumerate(reporter_calls, start=1)
        }
        if (
            len(reporter_calls) != conversion["reporter_model_calls"]
            or len(reporter_calls) > 4
            or reporter_finish["status"] != conversion["reporter_status"]
            or reporter_finish["reason_code"] != conversion["reporter_reason_code"]
        ):
            raise ValueError("reporter call or terminal ledger disagrees with trace")
        calls_per_cell[len(reporter_calls)] += 1
        terminal_reasons[reporter_finish["reason_code"] or reporter_finish["status"]] += 1
        reporter_tokens += sum(
            item["input_tokens"] + item["output_tokens"] for item in reporter_calls
        )

        validations = {
            item["result"]["finding_id"]: item["result"]
            for item in trace
            if item["type"] == "finding_validated"
        }
        integrated_roots = {
            validations[item["finding"]["finding_id"]]["matched_root_cause_id"]
            for item in trace
            if item["type"] == "finding_submitted"
            and item["sequence_number"] < reporter_start["sequence_number"]
            and validations[item["finding"]["finding_id"]]["status"] == "validated"
        }
        packet_path = state_dir / "reporter_bundles" / (run_id.replace("-", "") + ".json")
        packet = json.loads(packet_path.read_text())
        hash_to_evidence_id = {
            hashlib.sha256(item["evidence_id"].encode()).hexdigest(): item["evidence_id"]
            for item in packet["actions"]
        }
        retrieval_events = [item for item in trace if item["type"] == "reporter_evidence_retrieved"]
        for event in retrieval_events:
            k = call_number_by_id[event["model_call_id"]]
            retrievals_by_call[k] += 1
            if event["lookup_kind"] == "evidence":
                evidence_id = hash_to_evidence_id.get(event["lookup_key_sha256"])
                if evidence_id is None:
                    raise ValueError("exact evidence retrieval is outside frozen packet")
                retrieved_exact_ids.add((run_id, evidence_id))

        seen_roots = set(integrated_roots)
        novel_call_numbers = []
        reporter_findings = [item for item in trace if item["type"] == "reporter_finding_submitted"]
        if len(reporter_findings) != conversion["reporter_candidates"]:
            raise ValueError("reporter candidate count differs from prospective ledger")
        for event in reporter_findings:
            k = call_number_by_event[event["causation_id"]]
            submissions_by_call[k] += 1
            cited_ids.update((run_id, item) for item in event["source_evidence_ids"])
            validation = validations[event["finding_id"]]
            if validation["status"] == "validated":
                validated_by_call[k] += 1
                root = validation["matched_root_cause_id"]
                if root in seen_roots:
                    duplicate_roots_by_call[k] += 1
                else:
                    novel_roots_by_call[k] += 1
                    novel_call_numbers.append((k, event["sequence_number"]))
                    seen_roots.add(root)
            else:
                rejected_by_call[k] += 1
                rejected_reasons.update(validation["reason_codes"])
        novel_count = len(seen_roots - integrated_roots)
        if novel_count != conversion["incremental_reporter_roots"]:
            raise ValueError("call-attributed novel roots differ from prospective ledger")
        if novel_call_numbers:
            first_sequence = min(sequence for _, sequence in novel_call_numbers)
            first_novel.append(
                {
                    "call": min(
                        k for k, sequence in novel_call_numbers if sequence == first_sequence
                    ),
                    "retrievals_before": sum(
                        event["sequence_number"] < first_sequence for event in retrieval_events
                    ),
                }
            )

    total_novel = sum(novel_roots_by_call.values())
    total_submissions = sum(submissions_by_call.values())
    if reporter_tokens != sum(
        row["conversion"]["reporter_input_tokens"] + row["conversion"]["reporter_output_tokens"]
        for row in completed
    ):
        raise ValueError("reporter token total differs from prospective ledger")
    cumulative = 0
    curve = []
    for k in range(1, 5):
        cumulative += novel_roots_by_call[k]
        curve.append(
            {
                "call": k,
                "cumulative_incremental_roots": cumulative,
                "new_incremental_roots": novel_roots_by_call[k],
                "retrievals": retrievals_by_call[k],
                "submissions": submissions_by_call[k],
                "validated_submissions": validated_by_call[k],
                "root_duplicate_validated_submissions": duplicate_roots_by_call[k],
                "rejected_submissions": rejected_by_call[k],
                "cells_reaching_call": sum(
                    count for calls, count in calls_per_cell.items() if calls >= k
                ),
            }
        )
    return {
        "status": "exploratory_post_hoc_not_predeclared",
        "denominator_cells": len(completed),
        "terminal_reasons": dict(sorted(terminal_reasons.items())),
        "calls_per_cell": dict(sorted(calls_per_cell.items())),
        "fraction_call_budget": terminal_reasons["reporter_call_budget"] / len(completed),
        "reporter_tokens": reporter_tokens,
        "incremental_distinct_roots": total_novel,
        "validated_reporter_submissions": sum(validated_by_call.values()),
        "reporter_candidate_submissions": total_submissions,
        "novel_roots_per_100k_reporter_tokens": (
            total_novel * 100_000 / reporter_tokens if reporter_tokens else None
        ),
        "submission_novelty_rate": total_novel / total_submissions if total_submissions else None,
        "reporter_tokens_per_incremental_root": (
            reporter_tokens / total_novel if total_novel else None
        ),
        "cumulative_incremental_roots_by_call": curve,
        "first_incremental_finding": first_novel,
        "exact_retrieved_evidence_ids": len(retrieved_exact_ids),
        "exact_retrieved_evidence_ids_never_cited": len(retrieved_exact_ids - cited_ids),
        "cited_evidence_ids_without_exact_retrieval": len(cited_ids - retrieved_exact_ids),
        "rejected_reporter_reason_codes": dict(sorted(rejected_reasons.items())),
        "interpretation_limits": [
            "This post hoc analysis does not change the frozen primary estimator.",
            "Call attribution follows each reporter finding's causation event; "
            "validation occurs later.",
            "A root duplicate may cite a different valid witness, and is not "
            "necessarily an exact repeat.",
            "Exact evidence retrieval excludes search and entity lookups; citation "
            "without exact retrieval does not prove the reporter ignored evidence.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.journal, args.state_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
