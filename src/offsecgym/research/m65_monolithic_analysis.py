"""Predeclared, descriptive M6.5 control versus frozen M6.4 worker analysis."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from random import Random
from statistics import mean
from typing import Any

from offsecgym.research.m64_analysis import M64Observation
from offsecgym.research.m65_monolithic_matrix import PROTOCOL

ROOTS = 5
RESAMPLES = 10000
BOOTSTRAP_SEED = 6502
COMMON_BUDGETS = (120000, 160000)


def _interval(values: Sequence[float]) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = Random(BOOTSTRAP_SEED)
    estimates = sorted(
        mean(values[rng.randrange(len(values))] for _ in values) for _ in range(RESAMPLES)
    )
    return [estimates[249], estimates[9749]]


def _indexed(
    manifest: Mapping[str, Any], records: Sequence[Mapping[str, Any]], *, old: bool
) -> dict[tuple[str, int, str, int], tuple[M64Observation, Mapping[str, Any]]]:
    cells = {
        cell["cell_id"]: cell for cell in manifest["cells"] if not old or cell["policy_feasible"]
    }
    expected = manifest["planned_live_cells"]
    if len(cells) != expected or len(records) != expected:
        raise ValueError("analysis requires every planned live cell exactly once")
    indexed = {}
    run_ids = set()
    for record in records:
        cell = cells.get(record.get("cell_id"))
        if cell is None or record.get("type") != "cell_completed":
            raise ValueError("unknown or incomplete cell record")
        observation = M64Observation.model_validate(record["observation"])
        if (
            observation.cell_id != cell["cell_id"]
            or observation.experiment_sha256 != cell["experiment_sha256"]
            or record["order"] != cell["order"]
            or observation.run_id in run_ids
        ):
            raise ValueError("cell identity, hash, order, or run ID mismatch")
        run_ids.add(observation.run_id)
        if observation.evaluation.score_valid:
            evaluation = observation.evaluation
            if cell["variant"] == "vulnerable" and (
                evaluation.true_positives + evaluation.false_negatives != ROOTS
            ):
                raise ValueError("vulnerable root denominator changed")
            if cell["variant"] == "patched" and (
                evaluation.true_positives != 0 or evaluation.false_negatives != 0
            ):
                raise ValueError("patched cell contains active roots")
        arm = cell["arm"] if old else "bootstrapped_monolithic"
        budget = cell["worker_token_budget"] if old else cell["model_token_budget"]
        key = (arm, cell["range_seed"], cell["variant"], budget)
        if key in indexed:
            raise ValueError("duplicate arm/seed/variant/budget cell")
        indexed[key] = observation, record
    if len(indexed) != expected:
        raise ValueError("duplicate or missing analysis keys")
    return indexed


def _score(observation: M64Observation, variant: str) -> float:
    return (
        observation.evaluation.true_positives / ROOTS
        if variant == "vulnerable"
        else float(observation.evaluation.false_positives)
    )


def analyze_m65_monolithic(
    manifest: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    historical_manifest: Mapping[str, Any],
    historical_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Analyze complete journals; keep historical time/confounding explicit."""
    if (
        manifest.get("protocol") != PROTOCOL
        or historical_manifest.get("protocol") != "m64-v2-worker-primary-1"
    ):
        raise ValueError("analysis protocol mismatch")
    if tuple(manifest["seed_set"]) != tuple(historical_manifest["seed_set"]):
        raise ValueError("historical pairing seed set changed")
    if tuple(manifest["token_budgets"]) != tuple(historical_manifest["token_budgets"]):
        raise ValueError("historical budget curve changed")
    control = _indexed(manifest, records, old=False)
    workers = _indexed(historical_manifest, historical_records, old=True)
    revisions = {
        (item.resolved_model_revision, item.upstream_provider)
        for item, _ in [*control.values(), *workers.values()]
        if item.evaluation.score_valid
    }
    if len(revisions) != 1:
        raise ValueError("selected endpoint differs between scored cells")
    groups: dict[tuple[int, str], list[tuple[M64Observation, Mapping[str, Any]]]] = defaultdict(
        list
    )
    for (_, _, variant, budget), value in control.items():
        groups[(budget, variant)].append(value)
    strata = []
    for (budget, variant), rows in sorted(groups.items()):
        valid = [(item, record) for item, record in rows if item.evaluation.score_valid]
        tokens = sum(item.input_tokens + item.output_tokens for item, _ in valid)
        roots = sum(item.evaluation.true_positives for item, _ in valid)
        strata.append(
            {
                "budget": budget,
                "variant": variant,
                "planned_cells": len(rows),
                "score_valid_cells": len(valid),
                "invalid_statuses": sorted(
                    item.evaluation.status for item, _ in rows if not item.evaluation.score_valid
                ),
                "mean_root_recall": (
                    mean(_score(item, variant) for item, _ in valid)
                    if variant == "vulnerable" and valid
                    else None
                ),
                "mean_patched_false_findings": (
                    mean(_score(item, variant) for item, _ in valid)
                    if variant == "patched" and valid
                    else None
                ),
                "validated_roots_per_100k_reported_tokens": (
                    100000 * roots / tokens if variant == "vulnerable" and tokens else None
                ),
                "reported_model_tokens": tokens,
                "mean_candidates": (
                    mean(item.evaluation.candidate_count for item, _ in valid) if valid else None
                ),
                "mean_duplicate_validated_roots": (
                    mean(item.evaluation.duplicates for item, _ in valid) if valid else None
                ),
                "mean_elapsed_seconds": (
                    mean(record["duration_seconds"] for _, record in valid) if valid else None
                ),
            }
        )
    paired = []
    arms = tuple(historical_manifest["arm_configs"])
    for budget in manifest["token_budgets"]:
        for variant in ("vulnerable", "patched"):
            for arm in arms:
                differences = []
                for seed in manifest["seed_set"]:
                    left = control.get(("bootstrapped_monolithic", seed, variant, budget))
                    right = workers.get((arm, seed, variant, budget))
                    if left is None or right is None:
                        continue
                    if not left[0].evaluation.score_valid or not right[0].evaluation.score_valid:
                        continue
                    differences.append(_score(left[0], variant) - _score(right[0], variant))
                paired.append(
                    {
                        "control_minus": arm,
                        "budget": budget,
                        "variant": variant,
                        "historical_policy_feasible": any(
                            (arm, seed, variant, budget) in workers for seed in manifest["seed_set"]
                        ),
                        "matched_score_valid_seeds": len(differences),
                        "mean_difference": mean(differences) if differences else None,
                        "seed_bootstrap_95pct": _interval(differences),
                    }
                )
    auc = []
    for arm in arms:
        differences = []
        for seed in manifest["seed_set"]:
            left = [
                control.get(("bootstrapped_monolithic", seed, "vulnerable", budget))
                for budget in COMMON_BUDGETS
            ]
            right = [workers.get((arm, seed, "vulnerable", budget)) for budget in COMMON_BUDGETS]
            if any(
                value is None or not value[0].evaluation.score_valid for value in [*left, *right]
            ):
                continue
            differences.append(
                mean(_score(value[0], "vulnerable") for value in left)
                - mean(_score(value[0], "vulnerable") for value in right)
            )
        auc.append(
            {
                "control_minus": arm,
                "matched_score_valid_seed_curves": len(differences),
                "mean_normalized_auc_difference": mean(differences) if differences else None,
                "seed_bootstrap_95pct": _interval(differences),
            }
        )
    return {
        "protocol": PROTOCOL,
        "comparison": "matched historical; not randomized concurrent arms or fresh held-out seeds",
        "resolved_endpoints_scored": [list(pair) for pair in sorted(revisions)],
        "control_invalid_statuses": sorted(
            item.evaluation.status
            for item, _ in control.values()
            if not item.evaluation.score_valid
        ),
        "historical_invalid_statuses": sorted(
            item.evaluation.status
            for item, _ in workers.values()
            if not item.evaluation.score_valid
        ),
        "strata": strata,
        "paired_budget_differences": paired,
        "common_feasible_auc_differences": auc,
    }
