"""The T1.2 static matrix preserves A1 and cannot imply runtime qualification."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.benchmark.t12_static_audit import (
    audit_static_cells,
    dry_run,
    execution_matrix,
    qualification_source_pins,
)
from offsecgym.benchmark.t12_static_postcheck import verify
from offsecgym.benchmark.tasks import canonical_sha256

ROOT = Path(__file__).resolve().parents[2]
A1_BASELINE = ROOT / "docs/benchmark/t11f-a1-admission.json"
PUBLIC = ROOT / "docs/benchmark/t12-static-prequalification.json"


def test_dry_run_cli_is_independent_of_repository_import_path(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "research_ops/benchmark_t12_static_audit.py"), "--dry-run"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == json.loads(json.dumps(dry_run(ROOT)))


def test_dry_run_freezes_18_existing_task_ids_without_runtime() -> None:
    plan = dry_run(ROOT)
    matrix = execution_matrix()
    assert len(matrix) == 18
    assert len({task_id for _, _, _, task_id in matrix}) == 18
    assert plan["docker_starts"] == plan["gateway_actions"] == plan["provider_calls"] == 0
    for archetype, seed, variant, task_id in matrix:
        assert (
            task_id
            == make_task_manifest(
                archetype, task_mode="scoped_audit", variant=variant, seed=seed
            ).task.task_id
        )


def test_static_matrix_retains_every_cell_and_separates_controls(tmp_path: Path) -> None:
    before = A1_BASELINE.read_bytes()
    report = audit_static_cells(
        tmp_path / "first",
        a1_admission_path=A1_BASELINE,
        qualification_source_sha256=qualification_source_pins(ROOT),
    )
    again = audit_static_cells(
        tmp_path / "second",
        a1_admission_path=A1_BASELINE,
        qualification_source_sha256=qualification_source_pins(ROOT),
    )
    assert A1_BASELINE.read_bytes() == before
    assert report == again
    assert len(report["cells"]) == 18
    assert report["newly_qualified_archetypes"] == 0
    assert report["full_t12_qualification_executed"] is False
    assert all(cell["gates"]["Q1"] == "pass" for cell in report["cells"])
    assert all(cell["gates"]["Q2"] == "not_evaluated" for cell in report["cells"])
    assert all(cell["gates"]["Q3"] == "not_evaluated" for cell in report["cells"])
    expected_stages = {f"S{i}": "unknown" for i in range(7)}
    assert all(cell["stage_statuses"] == expected_stages for cell in report["cells"])
    for cell in report["cells"]:
        if cell["variant"] == "vulnerable":
            assert cell["q4_positive_reference_status"] == "not_evaluated"
            assert cell["patched_control_status"] == "not_applicable"
        else:
            assert cell["q4_positive_reference_status"] == "not_applicable"
            assert cell["patched_control_status"] == "not_evaluated"
            assert "patched_control" in cell["blockers"]


def test_changed_a1_baseline_is_rejected(tmp_path: Path) -> None:
    changed = json.loads(A1_BASELINE.read_text())
    changed["q9_readiness"] = "blocked"
    path = tmp_path / "changed-a1.json"
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="digest mismatch"):
        audit_static_cells(
            tmp_path / "state",
            a1_admission_path=path,
            qualification_source_sha256=qualification_source_pins(ROOT),
        )


def test_postcheck_rejects_patched_q4_success_even_with_rebuilt_digest(tmp_path: Path) -> None:
    report = json.loads(PUBLIC.read_text())
    patched = next(cell for cell in report["cells"] if cell["variant"] == "patched")
    patched["gates"]["Q4"] = "pass"
    patched["q4_positive_reference_status"] = "pass"
    report.pop("normalized_sha256")
    report["normalized_sha256"] = canonical_sha256(report)
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(report))
    result = verify(path, checkout=ROOT, a1_baseline=A1_BASELINE)
    assert result["checks"]["normalized_digest"]
    assert not result["checks"]["variant_semantics"]
    assert result["status"] == "fail"
