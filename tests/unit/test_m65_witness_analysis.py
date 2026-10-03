"""Study B analysis separates original findings from reporter recovery."""

from offsecgym.research.m64_stage_ledger import ROOT_KINDS
from offsecgym.research.m65_witness_analysis import analyze_witness_matrix


def test_analysis_keeps_incremental_roots_and_patched_false_findings_distinct() -> None:
    manifest = {
        "protocol": "m65-witness-recovery-v1",
        "seed_set": [2001],
        "cells": [
            {
                "cell_id": "v",
                "order": 1,
                "range_seed": 2001,
                "variant": "vulnerable",
                "experiment_sha256": "hv",
            },
            {
                "cell_id": "p",
                "order": 2,
                "range_seed": 2001,
                "variant": "patched",
                "experiment_sha256": "hp",
            },
        ],
    }
    root_rows = [
        {
            "root": root,
            "applicable": True,
            "ready": True,
            "admitted": True,
            "executed": root != "MEMBER-REFUND",
            "trace_proof": root != "MEMBER-REFUND",
            "submitted_original": False,
            "submitted_reporter": root == "DOC-CROSS-TENANT-READ",
            "validated_original": False,
            "validated_reporter": root == "DOC-CROSS-TENANT-READ",
            "incremental_reporter_root": root == "DOC-CROSS-TENANT-READ",
        }
        for root in ROOT_KINDS
    ]

    def record(cell, order, run, variant):
        return {
            "cell_id": cell,
            "order": order,
            "observation": {
                "run_id": run,
                "experiment_sha256": f"h{cell}",
                "input_tokens": 100,
                "output_tokens": 20,
                "evaluation": {"score_valid": True, "status": "completed"},
            },
            "conversion": {
                "run_id": run,
                "reporter_status": "completed",
                "original_validated_roots": 0,
                "incremental_reporter_roots": 1 if variant == "vulnerable" else 0,
                "original_false_findings": 1 if variant == "patched" else 0,
                "reporter_false_findings": 2 if variant == "patched" else 0,
                "reporter_duplicate_validated_submissions": 0,
                "reporter_input_tokens": 30,
                "reporter_output_tokens": 10,
                "rows": root_rows
                if variant == "vulnerable"
                else [{"root": root, "applicable": False} for root in ROOT_KINDS],
            },
        }

    result = analyze_witness_matrix(
        manifest, [record("v", 1, "rv", "vulnerable"), record("p", 2, "rp", "patched")]
    )
    assert result["vulnerable_incremental_reporter_roots"] == 1
    assert result["patched_original_false_findings"] == 1
    assert result["patched_reporter_false_findings"] == 2
    assert result["root_stage_counts"]["MEMBER-REFUND"]["executed"] == 0
    assert result["rows"][0]["probe_tokens"] == 80
