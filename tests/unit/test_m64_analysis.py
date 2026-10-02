"""The predeclared analysis preserves pairing, denominators, and failures."""

from pathlib import Path
from uuid import uuid4

import pytest

from offsecgym.evaluation import RunEvaluation, unscored_run
from offsecgym.research.m64_analysis import M64Observation, analyze_m64
from offsecgym.research.m64_matrix import plan_m64_matrix


def _sample():
    manifest = plan_m64_matrix(Path(__file__).parents[2], source_commit="analysis-test")
    observations = []
    roots = {"fixed_sequential": 0, "matched_parallel": 1, "opportunity_aware": 2}
    for cell in manifest["cells"]:
        if not cell["policy_feasible"]:
            continue
        vulnerable = cell["variant"] == "vulnerable"
        tp = roots[cell["arm"]] + int(cell["worker_token_budget"] == 160000) if vulnerable else 0
        fp = int(cell["arm"] == "opportunity_aware" and not vulnerable)
        evaluation = RunEvaluation(
            status="completed",
            score_valid=True,
            candidate_count=tp + fp,
            validated_count=tp,
            true_positives=tp,
            false_positives=fp,
            false_negatives=5 - tp if vulnerable else 0,
            duplicates=0,
            inconclusive=0,
            precision=None,
            recall=tp / 5 if vulnerable else None,
        )
        observations.append(
            M64Observation(
                cell_id=cell["cell_id"],
                experiment_sha256=cell["experiment_sha256"],
                run_id=uuid4(),
                evaluation=evaluation,
                input_tokens=1000,
                output_tokens=100,
                resolved_model_revision="kimi-k3-frozen-test",
                upstream_provider="moonshotai",
            )
        )
    return manifest, observations


def test_m64_analysis_uses_common_feasible_curve_and_paired_seeds() -> None:
    manifest, observations = _sample()
    report = analyze_m64(manifest, observations)
    assert report["planned_feasible_cells"] == 180
    assert report["structurally_infeasible_cells"] == 120
    auc = {item["arm"]: item for item in report["common_feasible_auc"]}
    assert auc["fixed_sequential"]["normalized_trapezoidal_auc"] == pytest.approx(0.1)
    assert auc["matched_parallel"]["normalized_trapezoidal_auc"] == pytest.approx(0.3)
    assert auc["opportunity_aware"]["normalized_trapezoidal_auc"] == pytest.approx(0.5)
    assert all(item["score_valid_seed_curves"] == 10 for item in auc.values())
    difference = next(
        item
        for item in report["paired_differences"]
        if item["left_arm"] == "fixed_sequential"
        and item["right_arm"] == "opportunity_aware"
        and item["budget"] == 120000
        and item["variant"] == "vulnerable"
    )
    assert difference["matched_score_valid_seeds"] == 10
    assert difference["mean_left_minus_right"] == pytest.approx(-0.4)
    auc_difference = next(
        item
        for item in report["paired_common_feasible_auc_differences"]
        if item["left_arm"] == "fixed_sequential" and item["right_arm"] == "opportunity_aware"
    )
    assert auc_difference["matched_score_valid_seed_curves"] == 10
    assert auc_difference["mean_normalized_auc_left_minus_right"] == pytest.approx(-0.4)
    infeasible = next(
        item
        for item in report["policy_opportunity_curve"]
        if item["arm"] == "fixed_sequential" and item["budget"] == 40000
    )
    assert infeasible["structurally_infeasible_seeds"] == 10
    assert infeasible["mean_policy_opportunity_recall"] == 0
    assert not infeasible["includes_model_behavior"]
    patched = next(
        item
        for item in report["strata"]
        if item["arm"] == "opportunity_aware"
        and item["budget"] == 40000
        and item["variant"] == "patched"
    )
    assert patched["mean_false_findings"] == 1


def test_m64_analysis_keeps_invalid_scores_out_of_paired_means() -> None:
    manifest, observations = _sample()
    chosen = next(
        item
        for item in observations
        if any(
            cell["cell_id"] == item.cell_id
            and cell["arm"] == "fixed_sequential"
            and cell["worker_token_budget"] == 120000
            and cell["variant"] == "vulnerable"
            for cell in manifest["cells"]
        )
    )
    observations[observations.index(chosen)] = chosen.model_copy(
        update={"evaluation": unscored_run("provider_failed")}
    )
    report = analyze_m64(manifest, observations)
    stratum = next(
        item
        for item in report["strata"]
        if item["arm"] == "fixed_sequential"
        and item["budget"] == 120000
        and item["variant"] == "vulnerable"
    )
    assert stratum["score_valid_cells"] == 9
    assert stratum["invalid_statuses"] == ["provider_failed"]
    paired = next(
        item
        for item in report["paired_differences"]
        if item["left_arm"] == "fixed_sequential"
        and item["right_arm"] == "matched_parallel"
        and item["budget"] == 120000
        and item["variant"] == "vulnerable"
    )
    assert paired["matched_score_valid_seeds"] == 9
    auc = next(item for item in report["common_feasible_auc"] if item["arm"] == "fixed_sequential")
    assert auc["score_valid_seed_curves"] == 9
    auc_paired = next(
        item
        for item in report["paired_common_feasible_auc_differences"]
        if item["left_arm"] == "fixed_sequential" and item["right_arm"] == "matched_parallel"
    )
    assert auc_paired["matched_score_valid_seed_curves"] == 9


def test_m64_analysis_rejects_incomplete_or_changed_protocol() -> None:
    manifest, observations = _sample()
    with pytest.raises(ValueError, match="incomplete"):
        analyze_m64(manifest, observations[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        analyze_m64(manifest, observations + observations[:1])
    changed = observations[0].model_copy(update={"resolved_model_revision": "other-revision"})
    with pytest.raises(ValueError, match="model revision"):
        analyze_m64(manifest, [changed, *observations[1:]])
