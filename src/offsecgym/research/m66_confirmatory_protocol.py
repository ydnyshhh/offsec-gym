"""Plan the unrun M6.6 confirmatory assignment without inspecting fixtures."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import yaml

from offsecgym.experiment.scripted import experiment_hash
from offsecgym.research.m66_confirmatory_analysis import ROOTS
from offsecgym.research.m66_confirmatory_order import schedule_blocks
from offsecgym.runtime.enterprise import COMPILER_VERSION as ENTERPRISE_COMPILER_VERSION
from offsecgym.runtime.saas import SAAS_V2_COMPILER_VERSION
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import model_tools

PROTOCOL = "m66-confirmatory-v1"
QUALIFIED_SOURCE_COMMIT = "950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d"
EXCLUSION_REGISTRY = "experiments/manifests/m66-prior-seed-exclusions-confirmatory-v1.json"
CONFIGS = {
    "saas": "experiments/configs/m66-pilot-v2-range-a.yaml",
    "enterprise_change_control_v1": "experiments/configs/m66-pilot-v2-range-b.yaml",
}
SOURCE_PATHS = (
    "src/offsecgym/solver/monolithic.py",
    "src/offsecgym/solver/range_surface.py",
    "src/offsecgym/experiment/witness_planning.py",
    "src/offsecgym/worldview/witness.py",
    "src/offsecgym/research/m66_pair.py",
    "src/offsecgym/runtime/enterprise.py",
    "src/offsecgym/runtime/saas.py",
)
PROTOCOL_PATHS = (
    "src/offsecgym/research/m66_confirmatory_protocol.py",
    "src/offsecgym/research/m66_confirmatory_analysis.py",
    "src/offsecgym/research/m66_confirmatory_attrition.py",
    "src/offsecgym/research/m66_confirmatory_extract.py",
    "src/offsecgym/research/m66_confirmatory_order.py",
    "src/offsecgym/research/m66_confirmatory_records.py",
    "src/offsecgym/research/m66_pilot_analysis.py",
    "research_ops/m66_confirmatory_precision.py",
    "research_ops/m66_confirmatory_freeze.py",
    "research_ops/m66_confirmatory_audit.py",
    "research_ops/m66_confirmatory_analyze.py",
    "research_ops/m66_confirmatory_stages.py",
    "research_ops/m66_extract_stages.py",
    "docs/diagnostics/m66-confirmatory-protocol-design.md",
    EXCLUSION_REGISTRY,
    *CONFIGS.values(),
)
ENDPOINT_URL = "https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints"
REQUESTED_MODEL = "moonshotai/kimi-k3"
SELECTED_REVISION = "moonshotai/kimi-k3-20260715"
UPSTREAM = "Moonshot AI"
INPUT_PRICE_PER_MILLION = 3.0
OUTPUT_PRICE_PER_MILLION = 15.0
MAX_CELL_TOKENS = 120_000
MAXIMUM_CONFIGURED_COST_USD = 280 * MAX_CELL_TOKENS * OUTPUT_PRICE_PER_MILLION / 1_000_000


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _source_bytes(root: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{commit}:{path}"], cwd=root, stderr=subprocess.DEVNULL
    )


def _full_commit(commit: str, label: str) -> None:
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ValueError(f"{label} must be a full immutable commit SHA")


def require_protocol_checkout(root: Path, protocol_commit: str) -> None:
    """Freeze only from the exact reviewed protocol checkout after CI/merge."""
    _full_commit(protocol_commit, "protocol_commit")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if head != protocol_commit:
        raise ValueError("manifest generation requires the exact protocol_commit checkout")
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root, text=True
    )
    if status:
        raise ValueError("manifest generation requires a clean protocol checkout")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", QUALIFIED_SOURCE_COMMIT, protocol_commit],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if ancestry.returncode != 0:
        raise ValueError("qualified source commit is not an ancestor of protocol commit")
    for path in SOURCE_PATHS:
        if (root / path).read_bytes() != _source_bytes(root, QUALIFIED_SOURCE_COMMIT, path):
            raise ValueError(f"qualified runtime source differs: {path}")
    for path in PROTOCOL_PATHS:
        if (root / path).read_bytes() != _source_bytes(root, protocol_commit, path):
            raise ValueError(f"protocol file differs from pinned commit: {path}")


def historical_exclusions(root: Path) -> set[int]:
    raw = (root / EXCLUSION_REGISTRY).read_bytes()
    registry = json.loads(raw)
    prior_path = root / registry["sources"]["prior_registry_path"]
    pilot_path = root / registry["sources"]["excluded_v2_pilot_manifest_path"]
    if (
        registry.get("schema_version") != "1"
        or registry.get("registry") != "prior_seed_exclusions"
        or registry.get("protocol") != PROTOCOL
        or _sha256(prior_path.read_bytes()) != registry["sources"]["prior_registry_sha256"]
        or _sha256(pilot_path.read_bytes())
        != registry["sources"]["excluded_v2_pilot_manifest_sha256"]
    ):
        raise ValueError("historical exclusion registry provenance differs")
    prior = set(json.loads(prior_path.read_bytes())["excluded_seeds"])
    pilot = json.loads(pilot_path.read_bytes())["seed_selection"]
    expected = prior | {pilot["range_a_seed"], pilot["range_b_seed"]}
    seeds = registry["excluded_seeds"]
    if (
        not isinstance(seeds, list)
        or any(type(seed) is not int or seed < 0 for seed in seeds)
        or seeds != sorted(expected)
    ):
        raise ValueError("historical exclusion snapshot differs from cited sources")
    return set(seeds)


def select_seeds(excluded: set[int]) -> dict[str, tuple[int, ...]]:
    """Select globally unique numeric seeds from frozen labels only."""
    used = set(excluded)
    selected = {}
    for family in ROOTS:
        seeds = []
        counter = 0
        while len(seeds) < 60:
            label = f"{PROTOCOL}|{family}|{counter}"
            candidate = (
                100_000
                + int.from_bytes(hashlib.sha256(label.encode()).digest()[:8], "big") % 800_000
            )
            counter += 1
            if candidate not in used:
                seeds.append(candidate)
                used.add(candidate)
        selected[family] = tuple(seeds)
    return selected


def patched_epochs() -> tuple[int, ...]:
    ranked = sorted(
        range(60), key=lambda epoch: hashlib.sha256(f"{PROTOCOL}|patched|{epoch}".encode()).digest()
    )
    return tuple(sorted(ranked[:10]))


def order_seed(label: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{PROTOCOL}|{label}".encode()).digest()[:8], "big")


def _base_spec(root: Path, family: str) -> ExperimentSpec:
    base = ExperimentSpec.model_validate(yaml.safe_load((root / CONFIGS[family]).read_bytes()))
    if (
        base.range.family != family
        or base.orchestrator != "bootstrapped_monolithic"
        or base.memory != "structured"
        or base.validation != "deterministic"
        or base.surface_visibility != "known_routes"
        or base.budget.max_total_tokens != MAX_CELL_TOKENS
        or base.budget.max_model_calls != 20
        or base.budget.max_output_tokens_per_call != 8192
        or base.budget.max_actions != (80 if family == "saas" else 120)
        or base.budget.max_http_requests != (80 if family == "saas" else 120)
        or base.bootstrap_budget is None
        or base.bootstrap_budget.max_actions != (32 if family == "saas" else 40)
        or base.bootstrap_budget.max_http_requests != base.bootstrap_budget.max_actions
        or base.model is None
        or base.model.provider != "openrouter"
        or base.model.name != REQUESTED_MODEL
        or base.model.upstream_provider != "moonshotai"
        or base.model.reasoning != "high"
        or base.model.input_usd_per_million_tokens != INPUT_PRICE_PER_MILLION
        or base.model.output_usd_per_million_tokens != OUTPUT_PRICE_PER_MILLION
    ):
        raise ValueError("confirmatory template changes the qualified model/range policy")
    return base


def _spec_for(base: ExperimentSpec, seed: int, patched: bool) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        base.model_copy(
            update={
                "seed": seed,
                "range": base.range.model_copy(update={"seed": seed, "patched": patched}),
            }
        ).model_dump(mode="python")
    )


def _price_snapshot(receipt: dict[str, object]) -> dict[str, object]:
    checked_at = datetime.fromisoformat(str(receipt.get("checked_at", "")).replace("Z", "+00:00"))
    if checked_at.utcoffset() is None:
        raise ValueError("endpoint price receipt needs a timezone")
    if (
        receipt.get("source") != ENDPOINT_URL
        or receipt.get("model_id") != REQUESTED_MODEL
        or receipt.get("endpoint") != f"{UPSTREAM} | {SELECTED_REVISION}"
        or receipt.get("upstream_provider") != UPSTREAM
        or receipt.get("revision") != SELECTED_REVISION
        or receipt.get("input_usd_per_million_tokens") != INPUT_PRICE_PER_MILLION
        or receipt.get("output_usd_per_million_tokens") != OUTPUT_PRICE_PER_MILLION
        or receipt.get("status") != 0
    ):
        raise ValueError("selected endpoint or price differs from qualified v2 policy")
    return receipt


def plan_confirmatory(
    root: Path, *, protocol_commit: str, endpoint_receipt: dict[str, object]
) -> dict[str, object]:
    """Build a deterministic plan; CLI enforces exact checkout before calling."""
    _full_commit(protocol_commit, "protocol_commit")
    excluded = historical_exclusions(root)
    seeds = select_seeds(excluded)
    patch = patched_epochs()
    family_seed = order_seed("family-order")
    arm_seed = order_seed("arm-order")
    blocks = schedule_blocks(seeds, patch, family_order_seed=family_seed, arm_order_seed=arm_seed)
    bases = {family: _base_spec(root, family) for family in ROOTS}
    cells = []
    for block_index, block in enumerate(blocks, 1):
        spec = _spec_for(bases[block.range_family], block.seed, block.variant == "patched")
        for arm in block.arm_order:
            order = len(cells) + 1
            cells.append(
                {
                    "order": order,
                    "block_index": block_index,
                    "epoch": block.epoch,
                    "cell_id": _sha256(
                        _canonical([PROTOCOL, block.range_family, block.seed, block.variant, arm])
                    )[:16],
                    "range_family": block.range_family,
                    "seed": block.seed,
                    "variant": block.variant,
                    "arm": arm,
                    "arm_order": list(block.arm_order),
                    "experiment_sha256": experiment_hash(spec),
                    "max_total_tokens": MAX_CELL_TOKENS,
                    "max_model_calls": spec.budget.max_model_calls,
                    "max_actions": spec.budget.max_actions,
                    "max_http_requests": spec.budget.max_http_requests,
                }
            )
    if len(cells) != 280 or len({cell["cell_id"] for cell in cells}) != 280:
        raise ValueError("confirmatory assignment must have 280 distinct cells")
    tools = {
        family: {
            arm: model_tools(structured=True, family=family, witness_planning=arm == "witness")
            for arm in ("control", "witness")
        }
        for family in ROOTS
    }
    return {
        "schema_version": "1",
        "protocol": PROTOCOL,
        "status": "frozen_unrun",
        "source_commit": QUALIFIED_SOURCE_COMMIT,
        "protocol_commit": protocol_commit,
        "paid_model_calls_authorized": False,
        "no_retry_or_replacement": True,
        "smallest_effect_of_interest": 0.20,
        "planned_trajectories": 280,
        "vulnerable_seed_pairs_per_family": 60,
        "patched_seed_pairs_per_family": 10,
        "assigned_roots_by_family": ROOTS,
        "seed_selection": {
            "method": (
                "SHA-256(m66-confirmatory-v1|family|counter), first eight bytes "
                "modulo 800000 plus 100000; globally unique after exclusion"
            ),
            "historical_exclusion_registry": EXCLUSION_REGISTRY,
            "historical_exclusion_registry_sha256": _sha256(
                (root / EXCLUSION_REGISTRY).read_bytes()
            ),
            "historical_exclusion_count": len(excluded),
            "seeds_by_family": seeds,
            "patched_epochs": patch,
            "fixture_inspection_before_selection": False,
        },
        "schedule": {
            "method": (
                "60 interleaved epochs; balanced A/B first and control/witness first; "
                "patched subset inserted in its epoch"
            ),
            "family_order_seed": family_seed,
            "arm_order_seed": arm_seed,
            "max_consecutive_same_family_blocks": 2,
            "block_count": len(blocks),
            "cell_assignment_sha256": _sha256(_canonical(cells)),
        },
        "cells": cells,
        "configs": {
            family: {"path": path, "sha256": _sha256((root / path).read_bytes())}
            for family, path in CONFIGS.items()
        },
        "source_file_hashes": {path: _sha256((root / path).read_bytes()) for path in SOURCE_PATHS},
        "protocol_file_hashes": {
            path: _sha256((root / path).read_bytes()) for path in PROTOCOL_PATHS
        },
        "tool_schema_sha256": _sha256(_canonical(tools)),
        "range_a_compiler_version": SAAS_V2_COMPILER_VERSION,
        "range_b_compiler_version": ENTERPRISE_COMPILER_VERSION,
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
        "price_snapshot": _price_snapshot(endpoint_receipt),
        "maximum_configured_estimated_cost_usd": MAXIMUM_CONFIGURED_COST_USD,
        "cumulative_estimated_cost_stop_usd": MAXIMUM_CONFIGURED_COST_USD,
        "provider_health_rule": {
            "isolated_transport_failure": "retain_audit_preflight_partner_continue",
            "pause_on_consecutive_transport_failures": 2,
            "pause_on_provider_failures_in_last_ten_started_cells": 3,
            "immediate_stop": [
                "endpoint_drift",
                "price_drift",
                "revision_drift",
                "upstream_drift",
                "auth_or_quota",
                "trace_integrity",
                "cost_cap",
            ],
            "unstarted_after_stop": "retain_missing_no_replacement",
        },
        "analysis_plan": {
            "primary": "all_assigned_vulnerable_root_pairs_by_family",
            "secondary": [
                "B1_B2_B3",
                "pooled_opportunity_weighted",
                "matched_score_valid",
                "patched_false_submissions",
            ],
            "missing": "no_point_or_bootstrap_if_unauditable; worst_best_bounds",
            "bootstrap_replicates": 10_000,
            "resampling_unit": "seed_pair",
        },
    }
