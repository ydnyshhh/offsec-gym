"""Pin a held-out, paired, monolithic-probe witness-recovery assay."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

import yaml

from offsecgym.experiment.reporter_recovery import combined_budget, recovery_experiment_hash
from offsecgym.research.m65_reporter import REPORTER_PROMPT, reporter_tools
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.saas import SAAS_V2_COMPILER_VERSION, SaasRangeCompiler
from offsecgym.schemas.specs import Budget, ExperimentSpec, RangeSpec, ReporterBudget

PROTOCOL = "m651-witness-recovery-v2"
PILOT_PROTOCOL = "m651-witness-recovery-pilot-v2"
SEED_SELECTION_PROTOCOL = "m651-witness-recovery-v1"
SEED_MANIFEST = "experiments/manifests/m651-witness-seed-selection-v1.json"
PROBE_CONFIG = "experiments/configs/kimi-k3-m651-witness-probe.yaml"
REPORTER_CONFIG = "experiments/configs/kimi-k3-m651-witness-reporter.yaml"
INPUT_USD_PER_MILLION = 3.0
OUTPUT_USD_PER_MILLION = 15.0
MAX_ESTIMATED_USD = 45.0
ORDER_LABEL = "m651-witness-recovery-v1-cell-order"
PINNED_SOURCE = (
    "src/offsecgym/experiment/scripted.py",
    "src/offsecgym/experiment/workers.py",
    "src/offsecgym/experiment/bootstrapped_monolithic.py",
    "src/offsecgym/experiment/reporter_recovery.py",
    "src/offsecgym/solver/monolithic.py",
    "src/offsecgym/solver/bootstrap_context.py",
    "src/offsecgym/experiment/bootstrap.py",
    "src/offsecgym/research/m65_reporter.py",
    "src/offsecgym/research/m65_witness_packet.py",
    "src/offsecgym/research/m65_conversion_ledger.py",
    "src/offsecgym/research/m65_witness_matrix.py",
    "src/offsecgym/research/m65_witness_execute.py",
    "src/offsecgym/research/m65_witness_analysis.py",
    "src/offsecgym/validation/deterministic.py",
    "src/offsecgym/runtime/saas.py",
    "src/offsecgym/schemas/events.py",
    "src/offsecgym/schemas/specs.py",
)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def maximum_budgeted_token_cost(
    budget: Budget,
    *,
    input_usd_per_million: float,
    output_usd_per_million: float,
) -> float:
    """Upper bound from total tokens and the explicit per-call output ceiling."""
    if (
        budget.max_total_tokens is None
        or budget.max_model_calls is None
        or budget.max_output_tokens_per_call is None
        or input_usd_per_million < 0
        or output_usd_per_million < input_usd_per_million
    ):
        raise ValueError("cost bound requires explicit caps and ordered nonnegative prices")
    output_cap = min(
        budget.max_total_tokens,
        budget.max_model_calls * budget.max_output_tokens_per_call,
    )
    return (
        (budget.max_total_tokens - output_cap) * input_usd_per_million
        + output_cap * output_usd_per_million
    ) / 1_000_000


def maximum_cell_token_cost(
    probe: Budget, reporter: ReporterBudget, price: Mapping[str, float]
) -> float:
    return maximum_budgeted_token_cost(
        probe,
        input_usd_per_million=price["input_usd_per_million"],
        output_usd_per_million=price["output_usd_per_million"],
    ) + maximum_budgeted_token_cost(
        reporter,
        input_usd_per_million=price["input_usd_per_million"],
        output_usd_per_million=price["output_usd_per_million"],
    )


def _committed_seed_manifest(root: Path) -> dict[str, object]:
    path = root / SEED_MANIFEST
    raw = path.read_bytes()
    committed = subprocess.run(
        ["git", "show", f"HEAD:{SEED_MANIFEST}"], cwd=root, check=True, capture_output=True
    ).stdout
    if raw != committed:
        raise ValueError("held-out seeds must be committed before any build is created")
    selection = json.loads(raw)
    seeds = selection.get("sample_seeds")
    pilot = selection.get("pilot_seed")
    excluded = selection.get("excluded_seeds")
    if (
        selection.get("protocol") != SEED_SELECTION_PROTOCOL
        or not isinstance(seeds, list)
        or len(seeds) != 10
        or len(set(seeds)) != 10
        or not isinstance(excluded, list)
        or set(seeds) & set(excluded)
        or pilot in seeds
        or pilot in excluded
    ):
        raise ValueError("held-out seed declaration is invalid")
    return selection


def spec_for_seed(base: ExperimentSpec, seed: int, patched: bool) -> ExperimentSpec:
    variant = "patched" if patched else "vulnerable"
    range_spec = RangeSpec.model_validate(
        {**base.range.model_dump(mode="json"), "seed": seed, "patched": patched}
    )
    return ExperimentSpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "name": f"m651_witness_{seed}_{variant}",
            "seed": seed,
            "range": range_spec.model_dump(mode="json"),
        }
    )


def plan_witness_matrix(root: Path, *, source_commit: str) -> dict[str, object]:
    if len(source_commit) != 40:
        raise ValueError("full source commit is required")
    selection = _committed_seed_manifest(root)
    seeds = tuple(selection["sample_seeds"])
    probe_path, reporter_path = root / PROBE_CONFIG, root / REPORTER_CONFIG
    base = ExperimentSpec.model_validate(yaml.safe_load(probe_path.read_text()))
    reporter = ReporterBudget.model_validate(yaml.safe_load(reporter_path.read_text()))
    if (
        base.range.scenario != "tenant_boundary_v2"
        or base.orchestrator != "bootstrapped_monolithic"
        or base.memory != "structured"
        or base.surface_visibility != "known_routes"
        or base.bootstrap_budget is None
        or base.budget.max_total_tokens != 120_000
        or base.budget.max_model_calls != 20
        or base.budget.max_output_tokens_per_call != 8192
        or base.budget.max_wall_seconds != 900
        or base.model is None
        or base.model.provider != "openrouter"
        or base.model.name != "moonshotai/kimi-k3"
        or base.model.reasoning != "high"
        or reporter.max_total_tokens != 80_000
        or reporter.max_model_calls != 4
        or reporter.max_output_tokens_per_call != 4096
        or reporter.max_retrieval_calls != 24
        or reporter.max_finding_submissions != 12
    ):
        raise ValueError("M6.5.1 configs differ from the monolithic recovery policy")
    combined = combined_budget(base.budget, reporter)
    if combined.max_total_tokens != 200_000 or combined.max_model_calls != 24:
        raise ValueError("combined model reservation changed")
    pair_records = {}
    # The committed seed declaration is checked above before entering this compiler block.
    with tempfile.TemporaryDirectory(prefix="offsecgym-m651-plan-") as temporary:
        compiler = SaasRangeCompiler(StateStore(Path(temporary)))
        for seed in seeds:
            vulnerable = compiler.build(spec_for_seed(base, seed, False).range)
            patched = compiler.build(spec_for_seed(base, seed, True).range)
            fixture = (compiler.state.build_dir(vulnerable.build_id) / "fixture.json").read_bytes()
            if (
                vulnerable.pair_id != patched.pair_id
                or fixture
                != (compiler.state.build_dir(patched.build_id) / "fixture.json").read_bytes()
            ):
                raise ValueError("prospective pair differs")
            pair_records[str(seed)] = {
                "pair_id": str(vulnerable.pair_id),
                "vulnerable_build_id": str(vulnerable.build_id),
                "patched_build_id": str(patched.build_id),
                "fixture_sha256": _digest(fixture),
            }
    order_seed = int.from_bytes(
        hashlib.sha256(
            (ORDER_LABEL + "|" + selection["selection_source_commit"]).encode()
        ).digest()[:8],
        "big",
    )
    blocks = [(seed, patched) for seed in seeds for patched in (False, True)]
    random.Random(order_seed).shuffle(blocks)
    cells = []
    for order, (seed, patched) in enumerate(blocks, start=1):
        variant = "patched" if patched else "vulnerable"
        spec = spec_for_seed(base, seed, patched)
        pair = pair_records[str(seed)]
        cells.append(
            {
                "cell_id": _digest(_canonical([PROTOCOL, seed, variant]))[:16],
                "order": order,
                "range_seed": seed,
                "variant": variant,
                "pair_id": pair["pair_id"],
                "build_id": pair[f"{variant}_build_id"],
                "experiment_sha256": recovery_experiment_hash(spec, reporter),
            }
        )
    price = {
        "input_usd_per_million": INPUT_USD_PER_MILLION,
        "output_usd_per_million": OUTPUT_USD_PER_MILLION,
    }
    per_cell_cost_ceiling = maximum_cell_token_cost(base.budget, reporter, price)
    worst_cost = round(len(cells) * per_cell_cost_ceiling, 6)
    if worst_cost > MAX_ESTIMATED_USD:
        raise ValueError("maximum configured cost exceeds the approved threshold")
    return {
        "protocol": PROTOCOL,
        "schema_version": "1",
        "status": "pilot_preflight_pending",
        "source_commit": source_commit,
        "seed_selection": {
            "path": SEED_MANIFEST,
            "sha256": _digest((root / SEED_MANIFEST).read_bytes()),
        },
        "seed_set": seeds,
        "pilot_seed": selection["pilot_seed"],
        "range_compiler_version": SAAS_V2_COMPILER_VERSION,
        "range_pairs": pair_records,
        "probe_config": {"path": PROBE_CONFIG, "sha256": _digest(probe_path.read_bytes())},
        "reporter_config": {"path": REPORTER_CONFIG, "sha256": _digest(reporter_path.read_bytes())},
        "source_files": {path: _digest((root / path).read_bytes()) for path in PINNED_SOURCE},
        "validator_sha256": _digest(
            (root / "src/offsecgym/validation/deterministic.py").read_bytes()
        ),
        "analysis_sha256": _digest(
            (root / "src/offsecgym/research/m65_witness_analysis.py").read_bytes()
        ),
        "evidence_bundle_schema_version": "3",
        "reporter_prompt_sha256": _digest(REPORTER_PROMPT.encode()),
        "reporter_tool_schema_sha256": _digest(_canonical(reporter_tools())),
        "probe_policy": "bootstrapped_monolithic_structured_m65_control",
        "reporter_contract": "m651-read-only-v1",
        "model_request": base.model.model_dump(mode="json"),
        "expected_selected_endpoint": {
            "revision": "moonshotai/kimi-k3-20260715",
            "upstream_provider": "Moonshot AI",
        },
        "price_snapshot": price,
        "per_cell_cost_ceiling_usd": round(per_cell_cost_ceiling, 6),
        "bootstrap_budget": base.bootstrap_budget.model_dump(mode="json"),
        "probe_budget": base.budget.model_dump(mode="json"),
        "reporter_budget": reporter.model_dump(mode="json"),
        "combined_model_budget": combined.model_dump(mode="json"),
        "order_seed": order_seed,
        "cells": cells,
        "planned_live_cells": len(cells),
        "maximum_estimated_token_cost_usd": worst_cost,
        "cumulative_estimated_cost_stop_usd": MAX_ESTIMATED_USD,
        "provider_failure_policy": "retain_without_retry_and_exclude_unscored_stage",
        "requires_separate_paid_approval": True,
        "stopping_rules": [
            "one probe and reporter run per cell; no silent retry",
            "stop on interrupted or unscored probe/reporter",
            "stop on selected endpoint or source drift",
            "stop on artifact, packet, bootstrap, or event replay failure",
            "stop before a next cell whose reserved worst-case cost exceeds the threshold",
        ],
    }


def plan_witness_pilot(root: Path, *, source_commit: str) -> dict[str, object]:
    """Pin a distinct non-sample pair using the same frozen implementation."""
    main = plan_witness_matrix(root, source_commit=source_commit)
    pilot_seed = main["pilot_seed"]
    base = ExperimentSpec.model_validate(yaml.safe_load((root / PROBE_CONFIG).read_text()))
    reporter = ReporterBudget.model_validate(yaml.safe_load((root / REPORTER_CONFIG).read_text()))
    with tempfile.TemporaryDirectory(prefix="offsecgym-m651-pilot-plan-") as temporary:
        compiler = SaasRangeCompiler(StateStore(Path(temporary)))
        vulnerable = compiler.build(spec_for_seed(base, pilot_seed, False).range)
        patched = compiler.build(spec_for_seed(base, pilot_seed, True).range)
        fixture = (compiler.state.build_dir(vulnerable.build_id) / "fixture.json").read_bytes()
        if (
            vulnerable.pair_id != patched.pair_id
            or fixture != (compiler.state.build_dir(patched.build_id) / "fixture.json").read_bytes()
        ):
            raise ValueError("pilot sibling build differs")
    pair = {
        "pair_id": str(vulnerable.pair_id),
        "vulnerable_build_id": str(vulnerable.build_id),
        "patched_build_id": str(patched.build_id),
        "fixture_sha256": _digest(fixture),
    }
    order_seed = int.from_bytes(
        hashlib.sha256((PILOT_PROTOCOL + "|" + str(pilot_seed)).encode()).digest()[:8], "big"
    )
    # The v1 pilot already exercised both siblings. This amendment uses one
    # non-sample vulnerable cell to stress the larger reporter context.
    blocks = [False]
    random.Random(order_seed).shuffle(blocks)
    cells = []
    for order, patched_flag in enumerate(blocks, start=1):
        variant = "patched" if patched_flag else "vulnerable"
        cells.append(
            {
                "cell_id": _digest(_canonical([PILOT_PROTOCOL, pilot_seed, variant]))[:16],
                "order": order,
                "range_seed": pilot_seed,
                "variant": variant,
                "pair_id": pair["pair_id"],
                "build_id": pair[f"{variant}_build_id"],
                "experiment_sha256": recovery_experiment_hash(
                    spec_for_seed(base, pilot_seed, patched_flag), reporter
                ),
            }
        )
    return {
        **main,
        "protocol": PILOT_PROTOCOL,
        "status": "non_sample_feasibility_recheck_not_executed",
        "amendment_of": {
            "protocol": "m651-witness-recovery-pilot-v1",
            "manifest_sha256": _digest(
                (root / "experiments/manifests/m651-witness-recovery-pilot-v1.json").read_bytes()
            ),
        },
        "seed_set": [pilot_seed],
        "range_pairs": {str(pilot_seed): pair},
        "order_seed": order_seed,
        "cells": cells,
        "planned_live_cells": 1,
        "maximum_estimated_token_cost_usd": main["per_cell_cost_ceiling_usd"],
        "cumulative_estimated_cost_stop_usd": main["per_cell_cost_ceiling_usd"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    planned = plan_witness_matrix(args.repository_root, source_commit=args.source_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(planned, indent=2, sort_keys=True) + "\n")
    print(f"planned {planned['planned_live_cells']} held-out cells")


if __name__ == "__main__":
    main()
