"""Plan a separate common-bootstrap monolithic control without model calls."""

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

PROTOCOL = "m65-common-bootstrap-monolithic-v1"
CONFIG = "experiments/configs/kimi-k3-m65-bootstrapped-monolithic.yaml"
HISTORICAL_MANIFEST = "experiments/manifests/m64-v2-worker-primary-1.json"
SEEDS = tuple(range(1001, 1011))
TOKEN_BUDGETS = (40000, 60000, 80000, 120000, 160000)
ORDER_SEED = 6501


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _cell_spec(base: ExperimentSpec, seed: int, patched: bool, tokens: int) -> ExperimentSpec:
    variant = "patched" if patched else "vulnerable"
    range_spec = RangeSpec.model_validate(
        {
            **base.range.model_dump(mode="json"),
            "scenario": "tenant_boundary_v2",
            "seed": seed,
            "patched": patched,
        }
    )
    budget = Budget.model_validate({**base.budget.model_dump(), "max_total_tokens": tokens})
    return ExperimentSpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "name": f"m65_monolithic_{seed}_{variant}_{tokens}",
            "range": range_spec.model_dump(mode="json"),
            "budget": budget.model_dump(mode="json"),
        }
    )


def plan_m65_monolithic_matrix(root: Path, *, source_commit: str) -> dict[str, object]:
    """Pin the retrospective paired builds and all 100 proposed control cells."""
    if not source_commit:
        raise ValueError("source commit is required")
    config_path = root / CONFIG
    historical_path = root / HISTORICAL_MANIFEST
    base = ExperimentSpec.model_validate(yaml.safe_load(config_path.read_text()))
    if (
        base.orchestrator != "bootstrapped_monolithic"
        or base.memory != "structured"
        or base.surface_visibility != "known_routes"
        or base.bootstrap_budget is None
        or base.range.scenario != "tenant_boundary_v2"
        or base.budget.max_wall_seconds != 900
    ):
        raise ValueError("control config differs from the planned information boundary")
    historical = json.loads(historical_path.read_text())
    if historical.get("protocol") != "m64-v2-worker-primary-1":
        raise ValueError("historical reference protocol changed")
    if (
        tuple(historical["seed_set"]) != SEEDS
        or tuple(historical["token_budgets"]) != TOKEN_BUDGETS
    ):
        raise ValueError("historical seed or budget curve differs")
    if base.bootstrap_budget.model_dump(mode="json") != historical["bootstrap_budget"]:
        raise ValueError("control bootstrap budget differs from worker study")
    if base.model.model_dump(mode="json") != historical["model_request"]:
        raise ValueError("control provider request differs from worker study")
    if base.budget.model_dump(mode="json", exclude={"max_total_tokens"}) != {
        **historical["worker_caps_except_tokens"],
        "max_concurrency": 1,
    }:
        raise ValueError("control agent caps differ from worker study")
    with tempfile.TemporaryDirectory(prefix="offsecgym-m65-plan-") as temporary:
        compiler = SaasRangeCompiler(StateStore(Path(temporary)))
        for seed in SEEDS:
            pair = historical["range_pairs"][str(seed)]
            range_spec = base.range.model_copy(update={"seed": seed, "patched": False})
            vulnerable = compiler.build(range_spec)
            patched = compiler.build(range_spec.model_copy(update={"patched": True}))
            fixture = (compiler.state.build_dir(vulnerable.build_id) / "fixture.json").read_bytes()
            if (
                str(vulnerable.pair_id) != pair["pair_id"]
                or str(patched.pair_id) != pair["pair_id"]
                or str(vulnerable.build_id) != pair["vulnerable_build_id"]
                or str(patched.build_id) != pair["patched_build_id"]
                or _digest(fixture) != pair["fixture_sha256"]
                or fixture
                != (compiler.state.build_dir(patched.build_id) / "fixture.json").read_bytes()
            ):
                raise ValueError(f"historical build pair drifted for seed {seed}")
    blocks = [
        (seed, patched, tokens)
        for seed in SEEDS
        for patched in (False, True)
        for tokens in TOKEN_BUDGETS
    ]
    random.Random(ORDER_SEED).shuffle(blocks)
    cells = []
    for order, (seed, patched, tokens) in enumerate(blocks, start=1):
        variant = "patched" if patched else "vulnerable"
        pair = historical["range_pairs"][str(seed)]
        spec = _cell_spec(base, seed, patched, tokens)
        cells.append(
            {
                "cell_id": _digest(
                    json.dumps([PROTOCOL, seed, variant, tokens], separators=(",", ":")).encode()
                )[:16],
                "order": order,
                "range_seed": seed,
                "variant": variant,
                "model_token_budget": tokens,
                "pair_id": pair["pair_id"],
                "build_id": pair[f"{variant}_build_id"],
                "experiment_sha256": experiment_hash(spec),
            }
        )
    return {
        "protocol": PROTOCOL,
        "status": "planned_not_executed",
        "source_commit": source_commit,
        "historical_comparison": True,
        "historical_manifest": {
            "path": HISTORICAL_MANIFEST,
            "sha256": _digest(historical_path.read_bytes()),
            "freeze_commit": "50c3374",
        },
        "range_compiler_version": SAAS_V2_COMPILER_VERSION,
        "range_pairs": historical["range_pairs"],
        "seed_set": SEEDS,
        "token_budgets": TOKEN_BUDGETS,
        "config": {"path": CONFIG, "sha256": _digest(config_path.read_bytes())},
        "model_request": base.model.model_dump(mode="json"),
        "expected_selected_endpoint": {
            "revision": "moonshotai/kimi-k3-20260715",
            "upstream_provider": "Moonshot AI",
            "basis": "M6.4 completed-call observations; reverify before collection",
        },
        "bootstrap_budget": base.bootstrap_budget.model_dump(mode="json"),
        "agent_caps_except_tokens": base.budget.model_dump(
            mode="json", exclude={"max_total_tokens"}
        ),
        "cells": cells,
        "planned_live_cells": len(cells),
        "maximum_configured_model_tokens": sum(cell["model_token_budget"] for cell in cells),
        "maximum_configured_model_calls": len(cells) * (base.budget.max_model_calls or 0),
        "usd_ceiling": None,
        "requires_price_and_pilot_review": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    manifest = plan_m65_monolithic_matrix(args.repository_root, source_commit=args.source_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(
        f"planned {manifest['planned_live_cells']} historical-control cells and "
        f"{manifest['maximum_configured_model_tokens']} configured model tokens"
    )


if __name__ == "__main__":
    main()
