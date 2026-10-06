"""Global block order balances arms and limits family clustering."""

from __future__ import annotations

from collections import Counter

import pytest

from offsecgym.research.m66_confirmatory_order import FAMILIES, schedule_blocks


def _seeds():
    return {
        family: tuple(range(1_000 + i * 100, 1_060 + i * 100)) for i, family in enumerate(FAMILIES)
    }


def test_schedule_interleaves_families_and_balances_two_randomizations() -> None:
    epochs = tuple(range(0, 60, 6))
    blocks = schedule_blocks(_seeds(), epochs, family_order_seed=66, arm_order_seed=660)
    assert blocks == schedule_blocks(_seeds(), epochs, family_order_seed=66, arm_order_seed=660)
    assert len(blocks) == 140
    assert sum(2 for _ in blocks) == 280
    assert Counter((b.range_family, b.variant) for b in blocks) == {
        (family, variant): count
        for family in FAMILIES
        for variant, count in (("vulnerable", 60), ("patched", 10))
    }
    assert all(
        not (blocks[i].range_family == blocks[i + 1].range_family == blocks[i + 2].range_family)
        for i in range(138)
    )
    first_family = [next(b.range_family for b in blocks if b.epoch == j) for j in range(60)]
    assert Counter(first_family) == {FAMILIES[0]: 30, FAMILIES[1]: 30}
    for family in FAMILIES:
        for variant, half in (("vulnerable", 30), ("patched", 5)):
            orders = Counter(
                b.arm_order for b in blocks if b.range_family == family and b.variant == variant
            )
            assert orders == {("control", "witness"): half, ("witness", "control"): half}
    assert {b.epoch for b in blocks if b.variant == "patched"} == set(epochs)
    assert all(
        blocks[i].seed == blocks[i + 2].seed
        for i in range(138)
        if blocks[i].range_family == blocks[i + 2].range_family
        and blocks[i].epoch == blocks[i + 2].epoch
    )


def test_schedule_rejects_invalid_seed_or_patched_subset() -> None:
    with pytest.raises(ValueError, match="60 distinct"):
        schedule_blocks(
            {family: (1,) * 60 for family in FAMILIES},
            tuple(range(10)),
            family_order_seed=1,
            arm_order_seed=2,
        )
    with pytest.raises(ValueError, match="10 sorted"):
        schedule_blocks(_seeds(), (0,) * 10, family_order_seed=1, arm_order_seed=2)
