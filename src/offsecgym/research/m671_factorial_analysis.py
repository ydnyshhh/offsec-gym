"""Deterministic seed-block descriptive analysis for the excluded M6.7.1 pilot."""

from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from statistics import mean

from offsecgym.research.m671_protocol import ARMS, ROOTS, VARIANTS, canonical

ENDPOINTS = {
    "B2-REVOKED-ROLE": (
        "decisive_root_action_attempted",
        "decisive_root_action_completed",
        "target_transition",
        "root_recorded_finding_after_transition",
    ),
    "B3-CANCELLED-JOB": (
        "decisive_root_action_attempted",
        "decisive_root_action_completed",
        "target_transition",
    ),
}
UPTAKE = (
    "evidence_catalog_calls",
    "provenance_preflight_calls",
    "experiments_started",
    "model_marked_experiments_completed",
    "model_marked_experiments_abandoned",
    "exact_response_repeat_count",
    "exact_reasoning_repeat_count",
    "exact_tool_batch_repeat_count",
    "repeated_post_batches",
    "repeated_get_only_batches",
)


def _rate(rows: list[dict[str, object]], key: str) -> dict[str, object]:
    known = [bool(row[key]) for row in rows if type(row.get(key)) is bool]
    yes = sum(known)
    n = len(rows)
    return {
        "assigned": n,
        "known": len(known),
        "positive": yes,
        "observed_rate": yes / len(known) if known else None,
        "all_assigned_rate": yes / n if len(known) == n and n else None,
        "missingness_bounds": [yes / n, (yes + n - len(known)) / n] if n else None,
    }


def _effects(y: dict[str, float]) -> dict[str, float]:
    y00, y10, y01, y11 = (y[arm] for arm in ARMS)
    return {
        "A_when_B0": y10 - y00,
        "A_when_B1": y11 - y01,
        "B_when_A0": y01 - y00,
        "B_when_A1": y11 - y10,
        "A_main": ((y10 - y00) + (y11 - y01)) / 2,
        "B_main": ((y01 - y00) + (y11 - y10)) / 2,
        "interaction": y11 - y10 - y01 + y00,
    }


def _interval(values: list[float]) -> list[float]:
    ordered = sorted(values)
    return [ordered[int(0.025 * (len(ordered) - 1))], ordered[int(0.975 * (len(ordered) - 1))]]


def _resource(rows: list[dict[str, object]], name: str) -> dict[str, object]:
    values: list[Decimal] = []
    for row in rows:
        value = row.get(name)
        if value is None or type(value) is bool:
            continue
        try:
            parsed = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"invalid {name} resource value") from exc
        if not parsed.is_finite() or parsed < 0:
            raise ValueError(f"invalid {name} resource value")
        values.append(parsed)
    return {"known": len(values), "sum": str(sum(values, Decimal(0)))}


def _paired(
    rows: list[dict[str, object]], endpoint: str, *, root: str, variant: str, replicates: int
) -> dict[str, object]:
    by_seed: dict[int, dict[str, bool]] = defaultdict(dict)
    for row in rows:
        value = row.get(endpoint)
        if type(value) is bool:
            by_seed[int(row["seed"])][str(row["arm"])] = value
    complete = sorted(seed for seed, arms in by_seed.items() if set(arms) == set(ARMS))
    if not complete:
        return {
            "complete_seed_blocks": 0,
            "contrasts": None,
            "descriptive_seed_block_intervals": None,
        }
    seed_effects = [_effects({arm: float(by_seed[seed][arm]) for arm in ARMS}) for seed in complete]
    names = tuple(seed_effects[0])
    observed = {name: mean(item[name] for item in seed_effects) for name in names}
    rng_seed = int.from_bytes(
        hashlib.sha256(canonical(["m671-seed-bootstrap-v1", root, variant, endpoint])).digest()[:8],
        "big",
    )
    rng = random.Random(rng_seed)
    draws = {name: [] for name in names}
    for _ in range(replicates):
        selected = [seed_effects[rng.randrange(len(complete))] for _ in complete]
        for name in names:
            draws[name].append(mean(item[name] for item in selected))
    return {
        "complete_seed_blocks": len(complete),
        "contrasts": observed,
        "descriptive_seed_block_intervals": {name: _interval(draws[name]) for name in names},
        "interval_label": "excluded feasibility; descriptive; not confirmatory inference",
    }


def analyze_factorial(
    rows: list[dict[str, object]], *, bootstrap_replicates: int = 10_000
) -> dict[str, object]:
    """Retain all assignments and analyze each root/variant independently."""
    if bootstrap_replicates < 1:
        raise ValueError("bootstrap replicates must be positive")
    if len(rows) != 128 or len({row["cell_id"] for row in rows}) != 128:
        raise ValueError("factorial analysis requires all 128 unique assignments")
    groups: dict[str, object] = {}
    for root, _ in ROOTS:
        root_groups: dict[str, object] = {}
        for variant in VARIANTS:
            variant_rows = [
                row for row in rows if row["root_id"] == root and row["variant"] == variant
            ]
            if len(variant_rows) != 32:
                raise ValueError("root/variant assignment count differs")
            arms: dict[str, object] = {}
            for arm in ARMS:
                arm_rows = [row for row in variant_rows if row["arm"] == arm]
                if len(arm_rows) != 8 or len({row["seed"] for row in arm_rows}) != 8:
                    raise ValueError("arm seed pairing differs")
                status = Counter(str(row.get("terminal_status", "unstarted")) for row in arm_rows)
                arms[arm] = {
                    "assigned": 8,
                    "started": sum(row.get("started") is True for row in arm_rows),
                    "unstarted": sum(row.get("started") is not True for row in arm_rows),
                    "trace_auditable": sum(row.get("trace_auditable") is True for row in arm_rows),
                    "score_valid": sum(row.get("score_valid") is True for row in arm_rows),
                    "resource_auditable": sum(
                        row.get("resource_auditable") is True for row in arm_rows
                    ),
                    "terminal_statuses": dict(sorted(status.items())),
                    "endpoints": {name: _rate(arm_rows, name) for name in ENDPOINTS[root]},
                    "uptake_and_repeats": {
                        name: {
                            "known": sum(type(row.get(name)) in {int, float} for row in arm_rows),
                            "sum": sum(
                                row[name] for row in arm_rows if type(row.get(name)) in {int, float}
                            ),
                        }
                        for name in UPTAKE
                    },
                    "resources": {
                        name: _resource(arm_rows, name)
                        for name in (
                            "model_calls",
                            "input_tokens",
                            "output_tokens",
                            "estimated_cost_usd",
                            "exploration_actions",
                        )
                    },
                    "patched_findings": (
                        {
                            name: sum(row[name] for row in arm_rows if type(row.get(name)) is int)
                            for name in ("successfully_recorded_findings", "validated_roots")
                        }
                        if variant == "patched"
                        else None
                    ),
                }
            root_groups[variant] = {
                "arms": arms,
                "seed_block_contrasts": {
                    name: _paired(
                        variant_rows,
                        name,
                        root=root,
                        variant=variant,
                        replicates=bootstrap_replicates,
                    )
                    for name in ENDPOINTS[root]
                },
            }
        groups[root] = root_groups
    return {
        "protocol": "m671-factorial-feasibility-v1",
        "excluded_from_confirmatory_analysis": True,
        "no_primary_cross_root_pooling": True,
        "p_values": None,
        "bootstrap_replicates": bootstrap_replicates,
        "resampling_unit": "seed_block",
        "groups": groups,
    }
