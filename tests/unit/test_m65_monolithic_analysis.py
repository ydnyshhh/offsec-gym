"""Historical comparisons preserve feasibility and invalid-score denominators."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from offsecgym.evaluation import RunEvaluation, unscored_run
from offsecgym.research.m64_analysis import M64Observation
from offsecgym.research.m65_monolithic_analysis import analyze_m65_monolithic
from offsecgym.research.m65_monolithic_matrix import plan_m65_monolithic_matrix


def _records(manifest, *, old: bool, invalid_order: int | None = None):
    records = []
    for cell in manifest["cells"]:
        if old and not cell["policy_feasible"]:
            continue
        variant = cell["variant"]
        valid = cell["order"] != invalid_order
        evaluation = (
            RunEvaluation(
                status="completed",
                score_valid=True,
                candidate_count=1 if variant == "vulnerable" else 0,
                validated_count=1 if variant == "vulnerable" else 0,
                true_positives=1 if variant == "vulnerable" else 0,
                false_positives=0,
                false_negatives=4 if variant == "vulnerable" else 0,
                duplicates=0,
                inconclusive=0,
                precision=1.0 if variant == "vulnerable" else None,
                recall=0.2 if variant == "vulnerable" else None,
            )
            if valid
            else unscored_run("provider_failed")
        )
        observation = M64Observation(
            cell_id=cell["cell_id"],
            experiment_sha256=cell["experiment_sha256"],
            run_id=uuid4(),
            evaluation=evaluation,
            input_tokens=100,
            output_tokens=10,
            resolved_model_revision="moonshotai/kimi-k3-20260715",
            upstream_provider="Moonshot AI",
        )
        records.append(
            {
                "type": "cell_completed",
                "cell_id": cell["cell_id"],
                "order": cell["order"],
                "observation": observation.model_dump(mode="json"),
                "duration_seconds": 12.5,
            }
        )
    return records


def test_m65_control_analysis_keeps_infeasible_and_invalid_cells_separate() -> None:
    root = Path(__file__).parents[2]
    control = plan_m65_monolithic_matrix(root, source_commit="38efa4a")
    historical = json.loads(
        (root / "experiments/manifests/m64-v2-worker-primary-1.json").read_text()
    )
    current_records = _records(control, old=False, invalid_order=1)
    old_records = _records(historical, old=True)
    result = analyze_m65_monolithic(control, current_records, historical, old_records)
    assert result["comparison"].startswith("matched historical")
    assert result["control_invalid_statuses"] == ["provider_failed"]
    assert len(result["strata"]) == 10
    assert len(result["paired_budget_differences"]) == 30
    low_fixed = next(
        row
        for row in result["paired_budget_differences"]
        if row["control_minus"] == "fixed_sequential"
        and row["budget"] == 40000
        and row["variant"] == "vulnerable"
    )
    assert low_fixed["historical_policy_feasible"] is False
    assert low_fixed["matched_score_valid_seeds"] == 0
    assert low_fixed["mean_difference"] is None
    low_opp = next(
        row
        for row in result["paired_budget_differences"]
        if row["control_minus"] == "opportunity_aware"
        and row["budget"] == 40000
        and row["variant"] == "vulnerable"
    )
    assert low_opp["matched_score_valid_seeds"] == 9
    assert low_opp["mean_difference"] == 0
    assert len(result["common_feasible_auc_differences"]) == 3
    with pytest.raises(ValueError, match="every planned live cell"):
        analyze_m65_monolithic(control, current_records[:-1], historical, old_records)
    changed = list(current_records)
    changed[0] = {
        **changed[0],
        "observation": {**changed[0]["observation"], "resolved_model_revision": "different"},
    }
    # The changed cell is unscored, so its absent endpoint cannot alter scored comparisons.
    assert analyze_m65_monolithic(control, changed, historical, old_records) == result
