"""Deterministic M6.6 excluded-pilot manifest; no range or model execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import yaml

from offsecgym.experiment.scripted import experiment_hash
from offsecgym.research.m651_seed_selection import EXPLICIT_EXCLUSIONS, _walk_seeds
from offsecgym.runtime.enterprise import COMPILER_VERSION as ENTERPRISE_COMPILER_VERSION
from offsecgym.runtime.enterprise import FAMILY as ENTERPRISE_FAMILY
from offsecgym.runtime.saas import SAAS_V2_COMPILER_VERSION
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import model_tools

PROTOCOL = "m66-pilot-v1"
SEED_MIN = 100_000
SEED_SPAN = 800_000
CONFIGS = {
    "saas": "experiments/configs/m66-pilot-range-a.yaml",
    ENTERPRISE_FAMILY: "experiments/configs/m66-pilot-range-b.yaml",
}
PINNED_SEEDS = {"saas": 124501, ENTERPRISE_FAMILY: 704929}
SOURCE_PATHS = {
    "model_policy": ("src/offsecgym/solver/monolithic.py",),
    "range_surface": ("src/offsecgym/solver/range_surface.py",),
    "witness_policy": (
        "src/offsecgym/experiment/witness_planning.py",
        "src/offsecgym/worldview/witness.py",
    ),
    "pair_runner": ("src/offsecgym/research/m66_pair.py",),
}
ANALYSIS_PATHS = (
    "src/offsecgym/research/m66_pilot_analysis.py",
    "src/offsecgym/research/m66_analysis.py",
    "research_ops/m66_extract_stages.py",
)
REQUESTED_MODEL = "moonshotai/kimi-k3"
SELECTED_REVISION = "moonshotai/kimi-k3-20260715"
UPSTREAM = "Moonshot AI"
INPUT_PRICE_PER_MILLION = 3.0
OUTPUT_PRICE_PER_MILLION = 15.0
PILOT_COST_STOP_USD = 15.0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def derive_seed(label: str, counter: int = 0) -> int:
    digest = hashlib.sha256(f"m66-pilot-{label}-v1|{counter}".encode()).digest()
    return SEED_MIN + int.from_bytes(digest[:8], "big") % SEED_SPAN


def arm_order(family: str, variant: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"{PROTOCOL}|{family}|{variant}".encode()).digest()
    return ("control", "witness") if digest[0] & 1 == 0 else ("witness", "control")


def historical_exclusions(root: Path) -> set[int]:
    """Exclude prior seeds while ignoring this pilot's own frozen config/manifest."""
    excluded = set(EXPLICIT_EXCLUSIONS) | set(range(100))
    for folder in (
        root / "experiments" / "manifests",
        root / "experiments" / "configs",
        root / "docs" / "diagnostics",
        root / ".offsecgym" / "diagnostics",
    ):
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if (
                not path.is_file()
                or path.name.startswith("m66-pilot-")
                or path.suffix not in {".json", ".jsonl", ".yaml", ".yml"}
                or path.stat().st_size > 2_000_000
            ):
                continue
            if path.suffix == ".jsonl":
                for line in path.read_text().splitlines():
                    if line.strip():
                        excluded.update(_walk_seeds(json.loads(line)))
                continue
            value = (
                json.loads(path.read_text())
                if path.suffix == ".json"
                else yaml.safe_load(path.read_text())
            )
            excluded.update(_walk_seeds(value))
    return excluded


def _file_bundle(root: Path, paths: tuple[str, ...]) -> dict[str, object]:
    members = {path: _sha256((root / path).read_bytes()) for path in paths}
    return {"files": members, "sha256": _sha256(_canonical(members))}


def _source_file(root: Path, source_commit: str, path: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{source_commit}:{path}"], cwd=root, stderr=subprocess.DEVNULL
    )


def plan_pilot(root: Path, *, source_commit: str, price_checked_at: str) -> dict[str, object]:
    if len(source_commit) != 40 or any(c not in "0123456789abcdef" for c in source_commit):
        raise ValueError("source_commit must be one immutable full SHA")
    checked = datetime.fromisoformat(price_checked_at.replace("Z", "+00:00"))
    if checked.utcoffset() is None:
        raise ValueError("price check needs a timezone")
    if (
        derive_seed("range-a") != PINNED_SEEDS["saas"]
        or derive_seed("range-b") != PINNED_SEEDS[ENTERPRISE_FAMILY]
    ):
        raise ValueError("pilot seed derivation differs from frozen selection")
    excluded = historical_exclusions(root)
    if set(PINNED_SEEDS.values()) & excluded or len(set(PINNED_SEEDS.values())) != 2:
        raise ValueError("pilot seed collides with prior study use")
    sources = {}
    for label, paths in SOURCE_PATHS.items():
        for path in paths:
            if _source_file(root, source_commit, path) != (root / path).read_bytes():
                raise ValueError(f"{label} differs from pinned source commit: {path}")
        sources[label] = _file_bundle(root, paths)
    tool_schemas = {
        family: {
            arm: model_tools(structured=True, family=family, witness_planning=arm == "witness")
            for arm in ("control", "witness")
        }
        for family in CONFIGS
    }
    schema_hash = _sha256(_canonical(tool_schemas))
    for tools in tool_schemas.values():
        control = {item["name"]: item for item in tools["control"]}
        witness = {item["name"]: item for item in tools["witness"]}
        if (
            set(witness) - set(control) != {"start_witness", "get_witness"}
            or {name: witness[name] for name in control} != control
        ):
            raise ValueError("control and witness base tool surfaces differ")
    qualification = json.loads(
        (root / "docs/diagnostics/m66-range-b-qualification.json").read_text()
    )
    configs = {}
    cells = []
    for family, relative in CONFIGS.items():
        raw = (root / relative).read_bytes()
        base = ExperimentSpec.model_validate(yaml.safe_load(raw))
        if (
            base.range.family != family
            or base.range.seed != PINNED_SEEDS[family]
            or base.budget.max_total_tokens != 120_000
            or base.budget.max_model_calls != 20
            or base.budget.max_output_tokens_per_call != 8192
            or base.budget.max_wall_seconds != 900
            or base.budget.max_actions != (80 if family == "saas" else 120)
            or base.budget.max_http_requests != (80 if family == "saas" else 120)
            or base.bootstrap_budget is None
            or base.bootstrap_budget.max_actions != 32
            or base.bootstrap_budget.max_http_requests != 32
            or base.bootstrap_budget.max_wall_seconds != 120
            or base.orchestrator != "bootstrapped_monolithic"
            or base.memory != "structured"
            or base.validation != "deterministic"
            or base.surface_visibility != "known_routes"
            or base.model is None
            or base.model.provider != "openrouter"
            or base.model.name != REQUESTED_MODEL
            or base.model.upstream_provider != "moonshotai"
            or base.model.reasoning != "high"
        ):
            raise ValueError("pilot configuration differs from frozen budget/model policy")
        configs[family] = {"path": relative, "sha256": _sha256(raw)}
        for variant in ("vulnerable", "patched"):
            spec = base.model_copy(
                update={"range": base.range.model_copy(update={"patched": variant == "patched"})}
            )
            order = arm_order(family, variant)
            for arm in order:
                cells.append(
                    {
                        "cell_id": _sha256(_canonical([PROTOCOL, family, variant, arm]))[:16],
                        "range_family": family,
                        "seed": PINNED_SEEDS[family],
                        "variant": variant,
                        "arm": arm,
                        "arm_order": list(order),
                        "experiment_sha256": experiment_hash(spec),
                        "max_total_tokens": spec.budget.max_total_tokens,
                        "max_model_calls": spec.budget.max_model_calls,
                        "max_actions": spec.budget.max_actions,
                        "max_http_requests": spec.budget.max_http_requests,
                    }
                )
    if len(cells) != 8 or len({cell["cell_id"] for cell in cells}) != 8:
        raise ValueError("pilot must contain exactly eight distinct trajectories")
    maximum_configured_cost = 8 * 120_000 * OUTPUT_PRICE_PER_MILLION / 1_000_000
    trace_analysis = _file_bundle(root, ANALYSIS_PATHS)
    return {
        "protocol": PROTOCOL,
        "schema_version": "1",
        "status": "frozen_unrun",
        "source_commit": source_commit,
        "range_a_compiler_version": SAAS_V2_COMPILER_VERSION,
        "range_b_compiler_version": ENTERPRISE_COMPILER_VERSION,
        "range_b_qualification_bundle_sha256": qualification["source_bundle_sha256"],
        "model_policy_sha256": sources["model_policy"]["sha256"],
        "range_surface_sha256": sources["range_surface"]["sha256"],
        "witness_policy_sha256": sources["witness_policy"]["sha256"],
        "pair_runner_sha256": sources["pair_runner"]["sha256"],
        "trace_analysis_sha256": trace_analysis["sha256"],
        "source_hashes": sources,
        "tool_schema_sha256": schema_hash,
        "trace_analysis": trace_analysis,
        "configs": configs,
        "seed_selection": {
            "method": "SHA-256(label|0), first eight bytes mod 800000 + 100000",
            "labels": ["m66-pilot-range-a-v1", "m66-pilot-range-b-v1"],
            "range_a_seed": PINNED_SEEDS["saas"],
            "range_b_seed": PINNED_SEEDS[ENTERPRISE_FAMILY],
            "excluded_from_confirmatory_sample": True,
            "reason": "m66_live_pilot",
            "prior_exclusion_count": len(excluded),
            "prior_exclusion_sha256": _sha256(_canonical(sorted(excluded))),
            "fixture_inspection_before_selection": False,
        },
        "model_request": {
            "provider": "openrouter",
            "name": REQUESTED_MODEL,
            "reasoning": "high",
            "upstream_provider": "moonshotai",
            "allow_fallbacks": False,
        },
        "expected_selected_endpoint": {
            "revision": SELECTED_REVISION,
            "upstream_provider": UPSTREAM,
        },
        "price_snapshot": {
            "checked_at": price_checked_at,
            "source": "https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints",
            "input_usd_per_million_tokens": INPUT_PRICE_PER_MILLION,
            "output_usd_per_million_tokens": OUTPUT_PRICE_PER_MILLION,
        },
        "maximum_configured_estimated_cost_usd": maximum_configured_cost,
        "cumulative_estimated_cost_stop_usd": PILOT_COST_STOP_USD,
        "planned_trajectories": 8,
        "arm_order_method": "SHA-256(protocol|family|variant), low bit of first byte",
        "cells": cells,
        "no_retry_or_replacement": True,
        "pilot_gates": "infrastructure_and_feasibility_only",
        "effect_size_is_not_a_pilot_gate": True,
        "paid_model_calls_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--price-checked-at", required=True)
    args = parser.parse_args()
    result = plan_pilot(
        args.repository_root,
        source_commit=args.source_commit,
        price_checked_at=args.price_checked_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
