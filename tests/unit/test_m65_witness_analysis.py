"""Prospective analysis keeps proof denominators and reporter costs separate."""

from offsecgym.research.m65_conversion_ledger import ROOTS
from offsecgym.research.m65_witness_analysis import analyze_witness_matrix

COUNT_KEYS = (
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
    "probe_exact_repeated_http_actions",
    "probe_model_calls",
    "probe_input_tokens",
    "probe_output_tokens",
    "reporter_model_calls",
    "reporter_input_tokens",
    "reporter_output_tokens",
    "reporter_retrieval_calls",
)
STAGES = (
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
)


def test_analysis_uses_recoverable_missed_roots_and_seed_bootstrap() -> None:
    cells = [
        {
            "cell_id": f"{seed}-{variant}",
            "order": 2 * (seed - 1) + (variant == "patched") + 1,
            "range_seed": seed,
            "variant": variant,
            "experiment_sha256": f"h{seed}-{variant}",
            "build_id": f"b{seed}-{variant}",
        }
        for seed in range(1, 11)
        for variant in ("vulnerable", "patched")
    ]
    manifest = {
        "protocol": "m651-witness-recovery-v1",
        "seed_set": list(range(1, 11)),
        "cells": cells,
    }
    records = []
    for cell in cells:
        vulnerable = cell["variant"] == "vulnerable"
        rows = []
        for root in ROOTS:
            values = {stage: False for stage in STAGES}
            if vulnerable and root == "DOC-CROSS-TENANT-READ":
                values.update(
                    complete_trace_proof=True,
                    recoverable_missed_root=True,
                    reporter_recovered_root=cell["range_seed"] == 1,
                    reporter_validated=cell["range_seed"] == 1,
                )
            if root == "MEMBER-REFUND":
                values["refund"] = {"complete_ordered_witness": False, "citation_error_codes": []}
            rows.append({"root": root, "applicable": vulnerable, **values})
        conversion = {
            "run_id": cell["cell_id"],
            "score_valid": True,
            "reporter_status": "completed",
            "rows": rows,
            "patched_reporter_false_classes": ["other_unclassified"] if not vulnerable else [],
            **{key: 0 for key in COUNT_KEYS},
        }
        conversion["reporter_input_tokens"] = 100
        conversion["reporter_output_tokens"] = 20
        conversion["incremental_reporter_roots"] = int(vulnerable and cell["range_seed"] == 1)
        conversion["reporter_rejected"] = int(not vulnerable)
        records.append(
            {
                "cell_id": cell["cell_id"],
                "order": cell["order"],
                "build_id": cell["build_id"],
                "observation": {
                    "run_id": cell["cell_id"],
                    "experiment_sha256": cell["experiment_sha256"],
                    "evaluation": {"score_valid": True, "status": "completed"},
                },
                "conversion": conversion,
            }
        )
    result = analyze_witness_matrix(manifest, records)
    assert result["primary"]["recoverable_missed_roots"] == 10
    assert result["primary"]["reporter_recovered_distinct_roots"] == 1
    assert result["primary"]["recovery_fraction"] == 0.1
    assert result["primary"]["paired_seed_bootstrap_95pct"] is not None
    assert result["overall"]["patched_reporter_false_findings"] == 10
    assert result["overall"]["reporter_input_tokens"] == 2000
    assert "DOC-CROSS-TENANT-READ" in result["markdown_root_table"]
