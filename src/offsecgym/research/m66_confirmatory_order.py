"""Deterministic, family-interleaved assignment order for M6.6 design."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from offsecgym.research.m66_confirmatory_analysis import ROOTS

FAMILIES = tuple(ROOTS)
ARMS = ("control", "witness")
VULNERABLE_EPOCHS = 60
PATCHED_EPOCHS = 10


@dataclass(frozen=True)
class ScheduledBlock:
    epoch: int
    range_family: str
    seed: int
    variant: Literal["vulnerable", "patched"]
    arm_order: tuple[str, str]


def schedule_blocks(
    seed_by_family: dict[str, tuple[int, ...]],
    patched_epochs: tuple[int, ...],
    *,
    family_order_seed: int,
    arm_order_seed: int,
) -> tuple[ScheduledBlock, ...]:
    """Create 140 adjacent two-arm blocks and interleave both families.

    Epochs are zero-based. The caller chooses seeds and patched epochs without
    fixture inspection and pins this function/version, both RNG seeds, and the
    resulting complete schedule in the final manifest.
    """
    if set(seed_by_family) != set(FAMILIES):
        raise ValueError("schedule requires exactly the two frozen families")
    if any(
        len(seeds) != VULNERABLE_EPOCHS
        or len(set(seeds)) != VULNERABLE_EPOCHS
        or any(type(seed) is not int or seed < 0 for seed in seeds)
        for seeds in seed_by_family.values()
    ):
        raise ValueError("each family requires 60 distinct nonnegative seeds")
    if (
        len(patched_epochs) != PATCHED_EPOCHS
        or patched_epochs != tuple(sorted(set(patched_epochs)))
        or any(
            type(epoch) is not int or not 0 <= epoch < VULNERABLE_EPOCHS for epoch in patched_epochs
        )
    ):
        raise ValueError("patched epochs require 10 sorted unique indices")
    if type(family_order_seed) is not int or type(arm_order_seed) is not int:
        raise ValueError("schedule RNG seeds must be integers")

    family_first = [FAMILIES[0]] * (VULNERABLE_EPOCHS // 2) + [FAMILIES[1]] * (
        VULNERABLE_EPOCHS // 2
    )
    random.Random(family_order_seed).shuffle(family_first)
    arm_rng = random.Random(arm_order_seed)
    arm_orders = {}
    for family in FAMILIES:
        for variant, count in (("vulnerable", VULNERABLE_EPOCHS), ("patched", PATCHED_EPOCHS)):
            orders = [ARMS] * (count // 2) + [ARMS[::-1]] * (count // 2)
            arm_rng.shuffle(orders)
            arm_orders[family, variant] = iter(orders)

    patched_set = set(patched_epochs)
    blocks = []
    for epoch in range(VULNERABLE_EPOCHS):
        first = family_first[epoch]
        family_order = (first, next(family for family in FAMILIES if family != first))
        for family in family_order:
            blocks.append(
                ScheduledBlock(
                    epoch,
                    family,
                    seed_by_family[family][epoch],
                    "vulnerable",
                    next(arm_orders[family, "vulnerable"]),
                )
            )
        if epoch in patched_set:
            for family in family_order:
                blocks.append(
                    ScheduledBlock(
                        epoch,
                        family,
                        seed_by_family[family][epoch],
                        "patched",
                        next(arm_orders[family, "patched"]),
                    )
                )
    return tuple(blocks)
