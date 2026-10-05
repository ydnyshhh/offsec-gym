"""Paired, seed-clustered reporting-context analysis on audited prefix records."""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from offsecgym.research.m64_stage_ledger import _load_actions, _trace_proof_assets
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import TraceEvent
from offsecgym.schemas.ground_truth import GroundTruthManifest

BOOTSTRAP_REPLICATES = 10_000


def source_complete_proof_roots(
    trace: list[TraceEvent],
    state_root: Path,
    oracle: GroundTruthManifest,
    fixture: dict[str, object],
) -> tuple[str, ...]:
    """Offline-only eligibility from the shared source trace, never either arm."""
    actions = _load_actions([item.model_dump(mode="json") for item in trace], state_root)
    roots = {
        str(prop.root_cause_id)
        for prop in oracle.properties
        if prop.active and _trace_proof_assets(prop, fixture, actions)
    }
    return tuple(sorted(roots))


class ReportingArmRecord(StrictModel):
    arm: Literal["fresh", "continuation"]
    source_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score_valid: bool
    status: str
    validated_roots: tuple[str, ...] = ()
    patched_false_findings: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    preflight_failures: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def unique_roots(self) -> ReportingArmRecord:
        if len(self.validated_roots) != len(set(self.validated_roots)):
            raise ValueError("arm root list contains duplicates")
        if not self.score_valid and self.validated_roots:
            raise ValueError("invalid arm cannot report scored roots")
        return self


class ReportingPrefixRecord(StrictModel):
    seed: int = Field(ge=0)
    variant: Literal["vulnerable", "patched"]
    source_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_score_valid: bool
    source_validated_roots: tuple[str, ...] = ()
    complete_proof_roots: tuple[str, ...] = ()
    fresh: ReportingArmRecord
    continuation: ReportingArmRecord

    @model_validator(mode="after")
    def same_prefix_and_arms(self) -> ReportingPrefixRecord:
        if (
            self.fresh.arm != "fresh"
            or self.continuation.arm != "continuation"
            or self.fresh.source_trace_sha256 != self.source_trace_sha256
            or self.continuation.source_trace_sha256 != self.source_trace_sha256
            or len(self.source_validated_roots) != len(set(self.source_validated_roots))
            or len(self.complete_proof_roots) != len(set(self.complete_proof_roots))
        ):
            raise ValueError("paired arms do not share one valid source prefix")
        if not self.source_score_valid and (
            self.source_validated_roots or self.complete_proof_roots
        ):
            raise ValueError("invalid source cannot provide scored roots")
        return self


def _cluster_interval(rows: list[tuple[int, int, int]], protocol: str) -> list[float] | None:
    if not rows or sum(row[0] for row in rows) == 0:
        return None
    seed = int.from_bytes(hashlib.sha256(protocol.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        draw = [rows[rng.randrange(len(rows))] for _ in rows]
        eligible = sum(row[0] for row in draw)
        if eligible:
            values.append(sum(row[1] - row[2] for row in draw) / eligible)
    if not values:
        return None
    values.sort()
    return [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]


def analyze_paired_reporting(
    rows: tuple[ReportingPrefixRecord, ...], *, protocol: str
) -> dict[str, object]:
    """Primary contrast uses only shared pretreatment proof opportunities.

    `complete_proof_roots` must be produced by a separate, source-only trace
    audit. This function never derives eligibility from either reporting arm.
    """
    if not rows or len({(row.seed, row.variant) for row in rows}) != len(rows):
        raise ValueError("paired analysis requires distinct seed/build prefixes")
    by_seed: dict[int, set[str]] = {}
    for row in rows:
        by_seed.setdefault(row.seed, set()).add(row.variant)
    if any(variants != {"vulnerable", "patched"} for variants in by_seed.values()):
        raise ValueError("every seed requires both vulnerable and patched prefixes")
    invalid: list[dict[str, object]] = []
    cluster_rows: list[tuple[int, int, int]] = []
    totals: Counter[str] = Counter()
    for row in sorted(rows, key=lambda item: (item.seed, item.variant)):
        valid = row.source_score_valid and row.fresh.score_valid and row.continuation.score_valid
        if not valid:
            invalid.append(
                {
                    "seed": row.seed,
                    "variant": row.variant,
                    "source_valid": row.source_score_valid,
                    "fresh_status": row.fresh.status,
                    "continuation_status": row.continuation.status,
                }
            )
            continue
        for arm in (row.fresh, row.continuation):
            prefix = arm.arm
            totals[f"{prefix}_input_tokens"] += arm.input_tokens
            totals[f"{prefix}_output_tokens"] += arm.output_tokens
            totals[f"{prefix}_model_calls"] += arm.model_calls
            totals[f"{prefix}_preflight_failures"] += arm.preflight_failures
        if row.variant == "patched":
            totals["fresh_patched_false_findings"] += row.fresh.patched_false_findings
            totals["continuation_patched_false_findings"] += row.continuation.patched_false_findings
            continue
        eligible = set(row.complete_proof_roots) - set(row.source_validated_roots)
        fresh = eligible & set(row.fresh.validated_roots)
        continuation = eligible & set(row.continuation.validated_roots)
        totals["eligible_roots"] += len(eligible)
        totals["fresh_recovered"] += len(fresh)
        totals["continuation_recovered"] += len(continuation)
        totals["fresh_only"] += len(fresh - continuation)
        totals["continuation_only"] += len(continuation - fresh)
        cluster_rows.append((len(eligible), len(fresh), len(continuation)))
    denominator = totals["eligible_roots"]
    return {
        "protocol": protocol,
        "resampling_unit": "seed pair",
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "seed_pairs": len(by_seed),
        "invalid_prefixes": invalid,
        "primary": {
            "eligible_roots": denominator,
            "fresh_recovered": totals["fresh_recovered"],
            "continuation_recovered": totals["continuation_recovered"],
            "difference_per_eligible_root": (
                (totals["fresh_recovered"] - totals["continuation_recovered"]) / denominator
                if denominator
                else None
            ),
            "seed_bootstrap_95pct": _cluster_interval(cluster_rows, protocol),
            "fresh_only": totals["fresh_only"],
            "continuation_only": totals["continuation_only"],
        },
        "resources_and_patched_controls": dict(totals),
    }
