"""Regenerate public, no-model R0 engineering reports from frozen sources."""

from __future__ import annotations

import hashlib
from pathlib import Path

from offsecgym.benchmark.task_diversity_audit import diversity_audit
from offsecgym.research.pilot.offline import empty_report
from offsecgym.research.pilot.protocol import admission_baseline, dry_run, load_protocol

A1_PARENT_SHA256 = "7076ad6f80c48fbb7c66b0ac2b2d7846884dee1f0e12ae70db80e13bb0584c16"
T12_PARENT_SHA256 = "8f15519a960ef08e008652bedb11d5f8ebeab8ac6b564861cd4bebef73d6da59"


def public_reports(checkout: Path) -> dict[str, dict[str, object]]:
    admission = checkout / "docs/benchmark/t11f-a1-admission.json"
    t12 = checkout / "docs/benchmark/t12-static-prequalification.json"
    protocol = load_protocol(checkout / "configs/research/r0-a1-pilot.yaml")
    plan = dry_run(protocol, admission)
    admission_status = admission_baseline(admission)
    if admission_status["sha256"] != A1_PARENT_SHA256:
        raise ValueError("parent A1 admission changed")
    if hashlib.sha256(t12.read_bytes()).hexdigest() != T12_PARENT_SHA256:
        raise ValueError("parent T1.2 report changed")
    diversity = diversity_audit()
    readiness: dict[str, object] = {
        "milestone": "R0",
        "protocol_version": protocol.version,
        "protocol_sha256": plan["protocol_sha256"],
        "protocol_ready_for_offline_review": True,
        "scoped_task_parity": False,
        "live_model_execution_ready": False,
        "live_execution_blocker": "generic_controller_objective",
        "offline_analysis_ready": True,
        "passive_provenance_ready": True,
        "task_diversity_audit_ready": True,
        "actual_model_runs": 0,
        "provider_calls": 0,
        "model_performance_claims": False,
        "new_archetypes_qualified": 0,
        "development_seeds": list(protocol.seeds),
        "variants": list(protocol.variants),
        "configured_model_count": len(protocol.models),
        "budget": protocol.budget.model_dump(mode="json"),
        "a1_admission_sha256": admission_status["sha256"],
        "t12_static_report_sha256": hashlib.sha256(t12.read_bytes()).hexdigest(),
    }
    postcheck: dict[str, object] = {
        "milestone": "R0",
        "scope": "source_bound_offline_engineering_only",
        "a1_admission_unchanged": True,
        "a1_admission_sha256": admission_status["sha256"],
        "a2_a4_qualification_unchanged": True,
        "t12_static_report_sha256": readiness["t12_static_report_sha256"],
        "a3_q3": "not_evaluated",
        "a3_q4": "not_evaluated",
        "a3_q5": "not_evaluated",
        "a3_q6": "not_evaluated",
        "historical_results_modified": False,
        "model_calls": 0,
        "docker_starts": 0,
    }
    return {
        "r0-pilot-readiness.json": readiness,
        "r0-task-diversity.json": diversity,
        "r0-postcheck.json": postcheck,
        "r0-empty-pilot-summary.json": empty_report(),
    }
