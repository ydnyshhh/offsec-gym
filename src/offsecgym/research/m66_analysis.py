"""Paired intention-to-treat analysis for generic temporal witness planning."""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from offsecgym.schemas.common import StrictModel

BOOTSTRAP_REPLICATES = 10_000


class WitnessPolicyRecord(StrictModel):
    seed: int = Field(ge=0)
    variant: Literal["vulnerable", "patched"]
    arm: Literal["control", "ledger"]
    score_valid: bool
    status: str
    assigned_state_change_roots: tuple[str, ...] = ()
    successful_transition_roots: tuple[str, ...] = ()
    complete_witness_roots: tuple[str, ...] = ()
    validated_roots: tuple[str, ...] = ()
    patched_false_findings: int = Field(default=0, ge=0)
    http_actions: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def stage_sets_are_bound(self) -> WitnessPolicyRecord:
        sets = (
            self.assigned_state_change_roots,
            self.successful_transition_roots,
            self.complete_witness_roots,
            self.validated_roots,
        )
        if any(len(items) != len(set(items)) for items in sets):
            raise ValueError("witness stage lists contain duplicates")
        assigned = set(self.assigned_state_change_roots)
        if (
            not set(self.successful_transition_roots) <= assigned
            or not set(self.complete_witness_roots) <= assigned
            or not set(self.complete_witness_roots) <= set(self.successful_transition_roots)
            or not set(self.validated_roots) <= assigned
            or not set(self.validated_roots) <= set(self.complete_witness_roots)
            or (self.variant == "patched" and assigned)
        ):
            raise ValueError("witness stages exceed assigned root opportunities")
        if not self.score_valid and any(sets[1:]):
            raise ValueError("invalid run cannot report scored witness stages")
        return self


def _interval(clusters: list[tuple[int, int, int]], protocol: str) -> list[float] | None:
    if not clusters or sum(row[0] for row in clusters) == 0:
        return None
    seed = int.from_bytes(hashlib.sha256(protocol.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sample = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        count = sum(x[0] for x in sample)
        if count:
            estimates.append(sum(x[1] - x[2] for x in sample) / count)
    estimates.sort()
    return (
        [
            estimates[int(0.025 * (len(estimates) - 1))],
            estimates[int(0.975 * (len(estimates) - 1))],
        ]
        if estimates
        else None
    )


def analyze_witness_policy(
    rows: tuple[WitnessPolicyRecord, ...], *, protocol: str
) -> dict[str, object]:
    keyed = {(row.seed, row.variant, row.arm): row for row in rows}
    seeds = {row.seed for row in rows}
    if not seeds or len(keyed) != len(rows) or len(rows) != 4 * len(seeds):
        raise ValueError("witness comparison requires one complete four-cell seed block")
    for seed in seeds:
        if any(
            (seed, variant, arm) not in keyed
            for variant in ("vulnerable", "patched")
            for arm in ("control", "ledger")
        ):
            raise ValueError("witness comparison has an incomplete seed block")
    invalid: list[dict[str, object]] = []
    clusters: list[tuple[int, int, int]] = []
    totals: Counter[str] = Counter()
    for seed in sorted(seeds):
        for variant in ("vulnerable", "patched"):
            control = keyed[seed, variant, "control"]
            ledger = keyed[seed, variant, "ledger"]
            if control.assigned_state_change_roots != ledger.assigned_state_change_roots:
                raise ValueError("assigned root opportunities differ between policy arms")
            if not control.score_valid or not ledger.score_valid:
                invalid.append(
                    {
                        "seed": seed,
                        "variant": variant,
                        "control_status": control.status,
                        "ledger_status": ledger.status,
                    }
                )
                continue
            for item in (control, ledger):
                totals[f"{item.arm}_http_actions"] += item.http_actions
                totals[f"{item.arm}_input_tokens"] += item.input_tokens
                totals[f"{item.arm}_output_tokens"] += item.output_tokens
            if variant == "patched":
                totals["control_patched_false_findings"] += control.patched_false_findings
                totals["ledger_patched_false_findings"] += ledger.patched_false_findings
                continue
            assigned = len(control.assigned_state_change_roots)
            control_complete = len(control.complete_witness_roots)
            ledger_complete = len(ledger.complete_witness_roots)
            totals["assigned_opportunities"] += assigned
            totals["control_complete_witnesses"] += control_complete
            totals["ledger_complete_witnesses"] += ledger_complete
            totals["control_successful_transitions"] += len(control.successful_transition_roots)
            totals["ledger_successful_transitions"] += len(ledger.successful_transition_roots)
            totals["control_validated_roots"] += len(control.validated_roots)
            totals["ledger_validated_roots"] += len(ledger.validated_roots)
            clusters.append((assigned, ledger_complete, control_complete))
    opportunities = totals["assigned_opportunities"]
    return {
        "protocol": protocol,
        "resampling_unit": "seed pair",
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "invalid_pairs": invalid,
        "primary": {
            "assigned_opportunities": opportunities,
            "ledger_complete_witnesses": totals["ledger_complete_witnesses"],
            "control_complete_witnesses": totals["control_complete_witnesses"],
            "difference_per_assigned_root": (
                (totals["ledger_complete_witnesses"] - totals["control_complete_witnesses"])
                / opportunities
                if opportunities
                else None
            ),
            "seed_bootstrap_95pct": _interval(clusters, protocol),
        },
        "diagnostic_conditional_transition_to_witness": {
            arm: (
                totals[f"{arm}_complete_witnesses"] / totals[f"{arm}_successful_transitions"]
                if totals[f"{arm}_successful_transitions"]
                else None
            )
            for arm in ("control", "ledger")
        },
        "resources_and_patched_controls": dict(totals),
    }
