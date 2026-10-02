"""Deterministic M6.4 worker-policy manifest; this module never calls a provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import tempfile
from pathlib import Path

import yaml

from offsecgym.experiment.scripted import experiment_hash
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.saas import SAAS_V2_COMPILER_VERSION, SaasRangeCompiler
from offsecgym.schemas.specs import Budget, ExperimentSpec, RangeSpec
from offsecgym.solver.workers import (
    MIN_MEANINGFUL_INPUT_TOKENS,
    WORKER_OBJECTIVES,
    WORKER_OUTPUT_CAP_TOKENS,
)

SEEDS = tuple(range(1001, 1011))
TOKEN_BUDGETS = (40000, 60000, 80000, 120000, 160000)
ARMS = {
    "fixed_sequential": "kimi-k3-m622-bootstrapped-sequential.yaml",
    "matched_parallel": "kimi-k3-m622-bootstrapped-parallel.yaml",
    "opportunity_aware": "kimi-k3-m631-admitted-sequential.yaml",
}
ORDER_SEED = 6401


def _json_digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _fixed_worker_floor(spec: ExperimentSpec) -> int:
    output = min(
        spec.budget.max_output_tokens_per_call or WORKER_OUTPUT_CAP_TOKENS,
        WORKER_OUTPUT_CAP_TOKENS,
    )
    return len(WORKER_OBJECTIVES) * (MIN_MEANINGFUL_INPUT_TOKENS + output)


def plan_m64_matrix(repository_root: Path, *, source_commit: str) -> dict[str, object]:
    """Return paired build pins and every proposed cell, including infeasible cells."""
    if not source_commit:
        raise ValueError("source_commit is required for a reviewable matrix")
    config_root = repository_root / "experiments" / "configs"
    bases = {
        arm: ExperimentSpec.model_validate(yaml.safe_load((config_root / name).read_text()))
        for arm, name in ARMS.items()
    }
    reference = bases["fixed_sequential"]
    for arm, spec in bases.items():
        if (
            spec.model != reference.model
            or spec.range != reference.range
            or spec.bootstrap_budget != reference.bootstrap_budget
            or spec.memory != reference.memory
            or spec.validation != reference.validation
            or spec.surface_visibility != reference.surface_visibility
        ):
            raise ValueError(f"{arm} differs on a controlled input")
        budget = spec.budget.model_dump()
        reference_budget = reference.budget.model_dump()
        budget.pop("max_concurrency")
        reference_budget.pop("max_concurrency")
        if budget != reference_budget:
            raise ValueError(f"{arm} differs on a controlled budget")
    with tempfile.TemporaryDirectory(prefix="offsecgym-m64-plan-") as temporary:
        compiler = SaasRangeCompiler(StateStore(Path(temporary)))
        pairs = {}
        for seed in SEEDS:
            base_range = RangeSpec.model_validate(
                {
                    **reference.range.model_dump(mode="json"),
                    "scenario": "tenant_boundary_v2",
                    "seed": seed,
                }
            )
            vulnerable = compiler.build(base_range)
            patched = compiler.build(base_range.model_copy(update={"patched": True}))
            vulnerable_fixture = (
                compiler.state.build_dir(vulnerable.build_id) / "fixture.json"
            ).read_bytes()
            patched_fixture = (
                compiler.state.build_dir(patched.build_id) / "fixture.json"
            ).read_bytes()
            if vulnerable.pair_id != patched.pair_id or vulnerable_fixture != patched_fixture:
                raise ValueError(f"seed {seed} did not produce a matched public pair")
            pairs[seed] = {
                "pair_id": str(vulnerable.pair_id),
                "vulnerable_build_id": str(vulnerable.build_id),
                "patched_build_id": str(patched.build_id),
                "fixture_sha256": hashlib.sha256(vulnerable_fixture).hexdigest(),
            }
    cells = []
    blocks: list[tuple[int, int, bool]] = [
        (seed, budget, patched)
        for seed in SEEDS
        for budget in TOKEN_BUDGETS
        for patched in (False, True)
    ]
    rng = random.Random(ORDER_SEED)
    rng.shuffle(blocks)
    next_order = 1
    for seed, tokens, patched in blocks:
        arms = list(ARMS)
        rng.shuffle(arms)
        for arm in arms:
            base = bases[arm]
            range_spec = RangeSpec.model_validate(
                {
                    **base.range.model_dump(mode="json"),
                    "scenario": "tenant_boundary_v2",
                    "seed": seed,
                    "patched": patched,
                }
            )
            budget = Budget.model_validate(
                {
                    **base.budget.model_dump(),
                    "max_total_tokens": tokens,
                    "max_wall_seconds": 900,
                }
            )
            variant = "patched" if patched else "vulnerable"
            spec = ExperimentSpec.model_validate(
                {
                    **base.model_dump(mode="json"),
                    "name": f"m64_{arm}_{seed}_{variant}_{tokens}",
                    "range": range_spec.model_dump(mode="json"),
                    "budget": budget.model_dump(mode="json"),
                }
            )
            floor = _fixed_worker_floor(spec) if arm != "opportunity_aware" else None
            feasible = floor is None or tokens >= floor
            cell = {
                "cell_id": _json_digest([arm, seed, variant, tokens])[:16],
                "order": next_order if feasible else None,
                "arm": arm,
                "range_seed": seed,
                "variant": variant,
                "worker_token_budget": tokens,
                "pair_id": pairs[seed]["pair_id"],
                "build_id": pairs[seed][f"{variant}_build_id"],
                "experiment_sha256": experiment_hash(spec),
                "policy_feasible": feasible,
                "infeasible_reason": ("fixed_worker_one_turn_floor" if not feasible else None),
                "required_minimum_tokens": floor,
            }
            cells.append(cell)
            if feasible:
                next_order += 1
    feasible_cells = [item for item in cells if item["policy_feasible"]]
    return {
        "protocol": "m64-v2-worker-primary-1",
        "status": "planned_not_executed",
        "source_commit": source_commit,
        "range_compiler_version": SAAS_V2_COMPILER_VERSION,
        "policy_baseline_commit": "f00e966",
        "seed_set": SEEDS,
        "token_budgets": TOKEN_BUDGETS,
        "arm_configs": {
            arm: {
                "path": f"experiments/configs/{name}",
                "sha256": hashlib.sha256((config_root / name).read_bytes()).hexdigest(),
                "orchestrator": bases[arm].orchestrator,
                "max_concurrency": bases[arm].budget.max_concurrency,
            }
            for arm, name in ARMS.items()
        },
        "model_request": reference.model.model_dump(mode="json") if reference.model else None,
        "resolved_model_revision": None,
        "bootstrap_budget": reference.bootstrap_budget.model_dump(mode="json")
        if reference.bootstrap_budget
        else None,
        "worker_caps_except_tokens": {
            **reference.budget.model_dump(
                mode="json", exclude={"max_total_tokens", "max_concurrency"}
            ),
            "max_wall_seconds": 900,
        },
        "range_pairs": {str(seed): value for seed, value in sorted(pairs.items())},
        "fixed_worker_minimum_tokens": _fixed_worker_floor(reference),
        "cells": cells,
        "planned_live_cells": len(feasible_cells),
        "structurally_infeasible_cells": len(cells) - len(feasible_cells),
        "maximum_live_model_tokens": sum(item["worker_token_budget"] for item in feasible_cells),
        "maximum_live_model_calls": len(feasible_cells) * (reference.budget.max_model_calls or 0),
        "usd_ceiling": None,
        "requires_price_and_pilot_review": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    manifest = plan_m64_matrix(args.repository_root, source_commit=args.source_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(
        f"planned {manifest['planned_live_cells']} live cells and "
        f"{manifest['structurally_infeasible_cells']} structural infeasibilities"
    )


if __name__ == "__main__":
    main()
