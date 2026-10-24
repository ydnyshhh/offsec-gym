"""Fail-closed A1 cohort admission from independently produced gate records."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import model_validator

from offsecgym.benchmark.a1_reference_record import A1ReferenceRecord
from offsecgym.benchmark.manifests import make_task_manifest, source_pins
from offsecgym.benchmark.q2_runtime_qualification import Q2QualificationRecord
from offsecgym.schemas.common import StrictModel

ADMISSION_VERSION = "t11f-a1-admission-v1"
SEEDS = (42, 43, 44)
VARIANTS = ("vulnerable", "patched")
GateStatus = Literal["pass", "fail", "unknown", "not_evaluated"]


class CohortCell(StrictModel):
    seed: int
    variant: Literal["vulnerable", "patched"]
    task_id: str
    gates: dict[str, GateStatus]
    stage_statuses: dict[str, str]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def complete(self) -> CohortCell:
        if set(self.gates) != {f"Q{index}" for index in range(1, 9)}:
            raise ValueError("Q1-Q8 are required for each cell")
        if set(self.stage_statuses) != {f"S{index}" for index in range(7)}:
            raise ValueError("S0-S6 are required for each cell")
        return self


def _load_report(
    payload: dict[str, object] | None,
    manifest,
    gate: str,
    qualification_pins: dict[str, str],
) -> bool:
    return bool(
        payload
        and payload.get("task_id") == manifest.task.task_id
        and payload.get("seed") == manifest.task.seed
        and payload.get("variant") == manifest.task.variant
        and payload.get("source_sha256") == source_pins()
        and (gate != "q2" or payload.get("real_docker_used") is True)
        and (gate == "q2" or payload.get("qualification_source_sha256") == qualification_pins)
    )


def admit_a1(
    *,
    static: dict[tuple[int, str], dict[str, object]],
    q2: dict[tuple[int, str], dict[str, object]],
    reference: dict[tuple[int, str], dict[str, object]],
    conformance: dict[str, object],
    qualification_pins: dict[str, str] | None = None,
) -> dict[str, object]:
    cells: list[CohortCell] = []
    pins = source_pins()
    implementation_pins = qualification_pins or {}
    control_rows = conformance.get("negative_controls", {})
    required_controls = {f"NC{index}" for index in range(1, 16)} | {"positive"}
    q5_complete = (
        isinstance(control_rows, dict)
        and set(control_rows) == required_controls
        and all(isinstance(row, dict) and row.get("pass") is True for row in control_rows.values())
        and conformance.get("positive_control_pass") is True
    )
    q5 = (
        "pass"
        if q5_complete
        and conformance.get("q5_status") == "pass"
        and conformance.get("source_sha256") == pins
        and conformance.get("qualification_source_sha256") == implementation_pins
        else "not_evaluated"
    )
    score_checks = conformance.get("score_checks", {})
    q6_complete = (
        isinstance(score_checks, dict)
        and set(score_checks)
        == {
            "event_replay_agrees",
            "duplicate_deduplicated",
            "rejected_count",
            "patched_recall_undefined",
            "infrastructure_unscored",
        }
        and all(value is True for value in score_checks.values())
    )
    q6 = (
        "pass"
        if q6_complete
        and conformance.get("q6_status") == "pass"
        and conformance.get("source_sha256") == pins
        and conformance.get("qualification_source_sha256") == implementation_pins
        else "not_evaluated"
    )
    for seed in SEEDS:
        for variant in VARIANTS:
            key = (seed, variant)
            manifest = make_task_manifest(
                "A1", task_mode="scoped_audit", variant=variant, seed=seed
            )
            stat = static.get(key, {})
            q2_row = q2.get(key)
            ref = reference.get(key)
            static_gates = stat.get("gates", {})
            q1 = static_gates.get("Q1", {}).get("status", "not_evaluated")
            q7 = static_gates.get("Q7", {}).get("status", "not_evaluated")
            q8 = static_gates.get("Q8", {}).get("status", "not_evaluated")
            if not _load_report(q2_row, manifest, "q2", implementation_pins):
                q2_status: GateStatus = "not_evaluated"
            else:
                q2_status = Q2QualificationRecord.model_validate(q2_row).q2_status
            if not _load_report(ref, manifest, "reference", implementation_pins):
                q3_status: GateStatus = "not_evaluated"
                q4_status: GateStatus = "not_evaluated"
                stages = {}
            else:
                qualified_reference = A1ReferenceRecord.model_validate(ref)
                q3_status = qualified_reference.q3_status
                q4_status = (
                    qualified_reference.q4_status
                    if variant == "vulnerable"
                    else qualified_reference.patched_control_status
                )
                if (
                    not qualified_reference.visibility_parity
                    or not qualified_reference.docker_cleanup_verified
                ):
                    q3_status = "unknown"
                    q4_status = "unknown"
                stages = {
                    row["stage_id"]: row["status"]
                    for row in ref.get("stage_results", [])
                    if isinstance(row, dict)
                }
            gates: dict[str, GateStatus] = {
                "Q1": q1,
                "Q2": q2_status,
                "Q3": q3_status,
                "Q4": q4_status,
                "Q5": q5,
                "Q6": q6,
                "Q7": q7,
                "Q8": q8 if q3_status == "pass" else "unknown",
            }
            blockers = tuple(gate for gate, gate_status in gates.items() if gate_status != "pass")
            cells.append(
                CohortCell(
                    seed=seed,
                    variant=variant,
                    task_id=manifest.task.task_id,
                    gates=gates,
                    stage_statuses={
                        f"S{index}": stages.get(f"S{index}", "unknown") for index in range(7)
                    },
                    blockers=blockers,
                )
            )
    all_pass = all(not cell.blockers for cell in cells)
    any_fail = any("fail" in cell.gates.values() for cell in cells)
    readiness = (
        "qualified"
        if all_pass
        else "blocked"
        if any_fail
        else "partially_qualified"
        if any("pass" in cell.gates.values() for cell in cells)
        else "not_evaluated"
    )
    body: dict[str, object] = {
        "milestone": "T1.1c-f",
        "benchmark_version": "0.1.0-draft",
        "archetype_id": "A1",
        "range_family": "saas",
        "scenario": "tenant_boundary_v2",
        "task_mode": "scoped_audit",
        "qualification_policy": ADMISSION_VERSION,
        "development_seeds": SEEDS,
        "cells": [cell.model_dump(mode="json") for cell in cells],
        "q9_readiness": readiness,
        "admitted": readiness == "qualified",
        "other_scoped_archetypes": {f"A{index}": "not_evaluated" for index in range(2, 6)}
        | {f"B{index}": "not_evaluated" for index in range(1, 4)},
        "unguided_tasks": {
            "saas": "not_evaluated",
            "enterprise_change_control_v1": "not_evaluated",
        },
        "source_sha256": pins,
        "qualification_source_sha256": implementation_pins,
        "provider_calls": 0,
        "historical_results_modified": False,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["normalized_sha256"] = hashlib.sha256(raw).hexdigest()
    return body
