"""Deterministic M6.7.1 design and fail-closed freeze/approval checks."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from offsecgym.research.m671_protocol import (
    ENDPOINT_URL,
    PROTOCOL,
    REGISTRY_PATH,
    REQUESTED_MODEL,
    REVISION,
    SOURCE_COMMIT,
    UPSTREAM,
    arm_definitions,
    assignment_rows,
    historical_exclusions,
    plan_manifest,
    read_private_paid_approval,
    require_clean_merge,
    require_paid_approval,
    select_seeds,
    validate_endpoint,
)

ROOT = Path(__file__).parents[2]


def receipt() -> dict[str, object]:
    return {
        "checked_at": "2026-10-15T00:00:00Z",
        "source": ENDPOINT_URL,
        "response_sha256": "a" * 64,
        "model_id": REQUESTED_MODEL,
        "endpoint": f"{UPSTREAM} | {REVISION}",
        "upstream_provider": UPSTREAM,
        "revision": REVISION,
        "status": 0,
        "tools_supported": True,
        "reasoning_supported": True,
        "input_usd_per_million_tokens": "3",
        "output_usd_per_million_tokens": "15",
    }


def test_authoritative_historical_union_and_seed_selection() -> None:
    excluded, sha = historical_exclusions(ROOT)
    raw = (ROOT / REGISTRY_PATH).read_bytes()
    assert len(excluded) == 434
    assert sha == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["excluded_seeds"] == sorted(excluded)
    seeds = select_seeds(excluded)
    assert seeds == select_seeds(excluded)
    assert len(seeds) == len(set(seeds)) == 8
    assert not set(seeds) & excluded
    assert select_seeds(frozenset()) != seeds or not set(seeds) & excluded


def test_registry_fails_on_historical_source_drift(tmp_path: Path) -> None:
    path = ROOT / REGISTRY_PATH
    (tmp_path / REGISTRY_PATH).parent.mkdir(parents=True)
    (tmp_path / REGISTRY_PATH).write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="historical exclusion source"):
        historical_exclusions(tmp_path)


def test_exact_128_assignments_and_schedule() -> None:
    seeds = select_seeds(historical_exclusions(ROOT)[0])
    rows = assignment_rows(seeds)
    assert rows == assignment_rows(seeds)
    assert len(rows) == 128
    assert len({row["cell_id"] for row in rows}) == 128
    assert len({row["pair_id"] for row in rows}) == 64
    assert Counter(row["arm"] for row in rows) == {
        arm: 32 for arm in ("A0B0", "A1B0", "A0B1", "A1B1")
    }
    assert all(len([row for row in rows if row["seed"] == seed]) == 16 for seed in seeds)
    assert Counter(row["root_id"] for row in rows) == {
        "B2-REVOKED-ROLE": 64,
        "B3-CANCELLED-JOB": 64,
    }
    assert Counter(
        (row["arm"], row["arm_position"])
        for row in rows
        if row["root_position"] == row["variant_position"] == 1
    ) == {(arm, pos): 2 for arm in ("A0B0", "A1B0", "A0B1", "A1B1") for pos in range(1, 5)}
    assert (
        max(
            sum(rows[j + k]["arm"] == rows[j]["arm"] for k in range(3))
            for j in range(len(rows) - 2)
        )
        <= 2
    )


def test_arm_schema_separation_and_fake_manifest() -> None:
    arms = arm_definitions(ROOT)
    assert len({item["tool_schema_sha256"] for item in arms.values()}) == 4
    assert [(arms[name]["A"], arms[name]["B"]) for name in ("A0B0", "A1B0", "A0B1", "A1B1")] == [
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ]
    manifest = plan_manifest(ROOT, protocol_commit="a" * 40, endpoint_receipt=receipt())
    assert manifest["planned_cells"] == 128
    assert manifest["planned_pairs"] == 64
    assert Decimal(manifest["maximum_configured_estimated_cost_usd"]) == Decimal("230.4")
    assert manifest["paid_model_calls_authorized"] is False
    assert manifest["excluded_from_confirmatory_analysis"] is True
    assert len({item["experiment_sha256"] for item in manifest["cells"]}) == 128
    assert manifest["source_commit"] == SOURCE_COMMIT


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "wrong"),
        ("status", 1),
        ("input_usd_per_million_tokens", "2.9"),
        ("output_usd_per_million_tokens", "15.1"),
        ("tools_supported", False),
        ("reasoning_supported", False),
        ("upstream_provider", "other"),
    ],
)
def test_endpoint_drift_fails(field: str, value: object) -> None:
    changed = {**receipt(), field: value}
    with pytest.raises(ValueError, match="drifted"):
        validate_endpoint(changed)


def test_manifest_freeze_rejects_unmerged_protocol() -> None:
    with pytest.raises(ValueError, match="exact protocol merge"):
        require_clean_merge(ROOT, "a" * 40)


def test_paid_gate_requires_exact_private_scope(tmp_path: Path) -> None:
    manifest = plan_manifest(ROOT, protocol_commit="a" * 40, endpoint_receipt=receipt())
    raw = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    cell = manifest["cells"][0]["cell_id"]
    approval = {
        "protocol": PROTOCOL,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "source_commit": SOURCE_COMMIT,
        "protocol_commit": "a" * 40,
        "approved_cell_ids": [row["cell_id"] for row in manifest["cells"]],
        "cumulative_estimated_cost_stop_usd": manifest["cumulative_estimated_cost_stop_usd"],
        "selected_endpoint": receipt()["endpoint"],
        "selected_revision": REVISION,
        "no_retry_or_replacement": True,
        "approver": "human",
        "approved_at": "2026-10-16T00:00:00Z",
    }
    require_paid_approval(
        approval,
        raw,
        cell_id=cell,
        started_cell_ids=set(),
        active_reservations=0,
        cumulative_estimated_cost_usd=Decimal(0),
    )
    for override in (
        {"manifest_sha256": "0" * 64},
        {"approved_cell_ids": []},
        {"no_retry_or_replacement": False},
        {"cumulative_estimated_cost_stop_usd": "999"},
        {"approved_at": "not-a-timestamp"},
    ):
        with pytest.raises(ValueError, match="approval"):
            require_paid_approval(
                {**approval, **override},
                raw,
                cell_id=cell,
                started_cell_ids=set(),
                active_reservations=0,
                cumulative_estimated_cost_usd=Decimal(0),
            )
    for started, reservations, cost in (
        ({cell}, 0, Decimal(0)),
        (set(), 1, Decimal(0)),
        (set(), 0, Decimal("230")),
    ):
        with pytest.raises(ValueError):
            require_paid_approval(
                approval,
                raw,
                cell_id=cell,
                started_cell_ids=started,
                active_reservations=reservations,
                cumulative_estimated_cost_usd=cost,
            )
    path = tmp_path / "approval.json"
    with pytest.raises(ValueError, match="absent"):
        read_private_paid_approval(path)
    path.write_text(json.dumps(approval))
    path.chmod(0o600)
    assert read_private_paid_approval(path) == approval
