"""Detached T1.2 task-matrix audit; no gateway, Docker start, or model execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from offsecgym.benchmark.manifests import make_task_manifest, source_pins
from offsecgym.benchmark.qualification import QualificationRecord, qualify_static
from offsecgym.benchmark.tasks import canonical_sha256

POLICY_VERSION = "t12-static-prequalification-v1"
ARCHETYPES = ("A2", "A3", "A4")
SEEDS = (42, 43, 44)
VARIANTS = ("vulnerable", "patched")
BOUND_FILES = (
    "src/offsecgym/benchmark/t12_static_audit.py",
    "research_ops/benchmark_t12_static_audit.py",
    "src/offsecgym/benchmark/qualification.py",
    "src/offsecgym/benchmark/manifests.py",
    "src/offsecgym/benchmark/a1_admission.py",
)


def qualification_source_pins(checkout: Path) -> dict[str, str]:
    """Bind the qualifier to files in an explicit checkout."""
    pins = {}
    for name in BOUND_FILES:
        pins[name] = hashlib.sha256((checkout / name).read_bytes()).hexdigest()
    return pins


def dry_run(checkout: Path) -> dict[str, object]:
    """Describe the static matrix without creating a runtime instance."""
    return {
        "qualification_policy": POLICY_VERSION,
        "archetypes": ARCHETYPES,
        "seeds": SEEDS,
        "variants": VARIANTS,
        "cells": [
            {"archetype_id": archetype, "seed": seed, "variant": variant, "task_id": task_id}
            for archetype, seed, variant, task_id in execution_matrix()
        ],
        "required_for_full_qualification": ("Q2", "Q3", "Q4", "Q5", "Q6"),
        "source_sha256": source_pins(),
        "qualification_source_sha256": qualification_source_pins(checkout),
        "report_destination": "docs/benchmark/t12-static-prequalification.json",
        "docker_starts": 0,
        "gateway_actions": 0,
        "provider_calls": 0,
    }


def execution_matrix() -> tuple[tuple[str, int, str, str], ...]:
    """Freeze all assigned cells and their existing T0 identities."""
    return tuple(
        (
            archetype,
            seed,
            variant,
            make_task_manifest(
                archetype, task_mode="scoped_audit", variant=variant, seed=seed
            ).task.task_id,
        )
        for archetype in ARCHETYPES
        for seed in SEEDS
        for variant in VARIANTS
    )


def _a1_baseline(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    record = json.loads(raw)
    claimed = record.pop("normalized_sha256")
    if canonical_sha256(record) != claimed:
        raise ValueError("A1 admission digest mismatch")
    if record.get("q9_readiness") != "qualified" or len(record.get("cells", [])) != 6:
        raise ValueError("A1 baseline is incomplete")
    if record.get("source_sha256") != source_pins():
        raise ValueError("A1 baseline source pins differ from this checkout")
    for cell in record["cells"]:
        if cell["variant"] == "patched":
            if cell["gates"]["Q4"] != "not_applicable" or cell["patched_control_status"] != "pass":
                raise ValueError("A1 patched-control interpretation changed")
        elif cell["gates"]["Q4"] != "pass":
            raise ValueError("A1 vulnerable reference success changed")
    return {
        "admission_sha256": hashlib.sha256(raw).hexdigest(),
        "normalized_sha256": claimed,
        "q9_readiness_as_reported": "qualified",
        "cell_count": 6,
    }


def audit_static_cells(
    private_root: Path,
    *,
    a1_admission_path: Path,
    qualification_source_sha256: dict[str, str],
) -> dict[str, object]:
    """Run only the pre-existing compiler checks; retain every assigned cell."""
    baseline = _a1_baseline(a1_admission_path)
    pins = source_pins()
    cells: list[dict[str, object]] = []
    for archetype, seed, variant, task_id in execution_matrix():
        manifest = make_task_manifest(
            archetype, task_mode="scoped_audit", variant=variant, seed=seed
        )
        static = qualify_static(manifest, private_root / archetype / str(seed) / variant)
        if not isinstance(static, QualificationRecord):
            raise ValueError("static qualifier returned an invalid record")
        if static.task_id != task_id or static.source_sha256 != pins:
            raise ValueError("static qualifier task or source binding mismatch")
        gates = {name: static.gates[name].status for name in ("Q1", "Q7", "Q8")}
        gates.update({name: "not_evaluated" for name in ("Q2", "Q3", "Q5", "Q6")})
        gates["Q4"] = "not_evaluated" if variant == "vulnerable" else "not_applicable"
        patched_control = "not_applicable" if variant == "vulnerable" else "not_evaluated"
        blockers = tuple(
            name
            for name in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8")
            if gates[name] != "pass" and gates[name] != "not_applicable"
        ) + (("patched_control",) if variant == "patched" else ())
        q9_readiness = (
            "partially_qualified"
            if all(gates[name] == "pass" for name in ("Q1", "Q7", "Q8"))
            else "blocked"
        )
        cells.append(
            {
                "archetype_id": archetype,
                "seed": seed,
                "variant": variant,
                "task_id": task_id,
                "gates": gates,
                "q4_positive_reference_status": gates["Q4"],
                "patched_control_status": patched_control,
                "q9_readiness": q9_readiness,
                "stage_statuses": {f"S{index}": "unknown" for index in range(7)},
                "blockers": blockers,
                "real_docker_used": False,
                "gateway_used": False,
            }
        )
    if len(cells) != 18 or len({cell["task_id"] for cell in cells}) != 18:
        raise ValueError("T1.2 static matrix is incomplete or contains duplicate task IDs")
    archetype_status = {
        archetype: (
            "static_pass_runtime_unqualified"
            if all(
                cell["gates"][name] == "pass"
                for cell in cells
                if cell["archetype_id"] == archetype
                for name in ("Q1", "Q7", "Q8")
            )
            else "static_blocked"
        )
        for archetype in ARCHETYPES
    }
    result: dict[str, object] = {
        "milestone": "T1.2 static prequalification",
        "qualification_policy": POLICY_VERSION,
        "scope": "detached_compiler_and_admission_matrix_only",
        "parent_a1_baseline": baseline,
        "cells": cells,
        "archetype_status": archetype_status,
        "qualified_scoped_archetypes_from_parent_report": 1,
        "newly_qualified_archetypes": 0,
        "full_t12_qualification_executed": False,
        "source_sha256": pins,
        "qualification_source_sha256": qualification_source_sha256,
        "provider_calls": 0,
        "historical_results_modified": False,
    }
    result["normalized_sha256"] = canonical_sha256(result)
    return result
