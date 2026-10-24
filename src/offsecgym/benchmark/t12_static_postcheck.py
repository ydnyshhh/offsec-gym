"""Verify a sanitized T1.2 static report against its source checkout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from offsecgym.benchmark.manifests import source_pins
from offsecgym.benchmark.t12_static_audit import execution_matrix, qualification_source_pins
from offsecgym.benchmark.tasks import canonical_sha256


def verify(report_path: Path, *, checkout: Path, a1_baseline: Path) -> dict[str, object]:
    report = json.loads(report_path.read_text())
    claimed = report.pop("normalized_sha256")
    matrix = execution_matrix()
    expected = {(archetype, seed, variant, task_id) for archetype, seed, variant, task_id in matrix}
    cells = report.get("cells", [])
    observed = {
        (cell["archetype_id"], cell["seed"], cell["variant"], cell["task_id"]) for cell in cells
    }
    a1_bytes = a1_baseline.read_bytes()
    checks = {
        "normalized_digest": canonical_sha256(report) == claimed,
        "source_pins": report.get("source_sha256") == source_pins()
        and report.get("qualification_source_sha256") == qualification_source_pins(checkout),
        "full_18_cell_matrix": len(cells) == 18 and observed == expected,
        "static_gates": all(
            all(cell["gates"][name] == "pass" for name in ("Q1", "Q7", "Q8")) for cell in cells
        ),
        "runtime_gates_unearned": all(
            all(cell["gates"][name] == "not_evaluated" for name in ("Q2", "Q3", "Q5", "Q6"))
            and cell["q9_readiness"] == "partially_qualified"
            and not cell["real_docker_used"]
            and not cell["gateway_used"]
            for cell in cells
        ),
        "variant_semantics": all(
            cell["q4_positive_reference_status"] == cell["gates"]["Q4"]
            and (
                cell["gates"]["Q4"] == "not_evaluated"
                and cell["patched_control_status"] == "not_applicable"
                if cell["variant"] == "vulnerable"
                else cell["gates"]["Q4"] == "not_applicable"
                and cell["patched_control_status"] == "not_evaluated"
                and "patched_control" in cell["blockers"]
            )
            for cell in cells
        ),
        "a1_baseline_unchanged": report["parent_a1_baseline"]["admission_sha256"]
        == hashlib.sha256(a1_bytes).hexdigest(),
        "no_new_admission": report["newly_qualified_archetypes"] == 0
        and report["full_t12_qualification_executed"] is False,
        "no_provider_or_historical_change": report["provider_calls"] == 0
        and report["historical_results_modified"] is False,
    }
    return {
        "milestone": "T1.2 static prequalification",
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "postcheck_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
