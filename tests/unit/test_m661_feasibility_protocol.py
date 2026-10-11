"""Deterministic and fail-closed gates for the excluded checkpoint feasibility plan."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from offsecgym.research import m661_feasibility_protocol as protocol
from offsecgym.research.m661_seed_exclusions import load_exclusion_registry

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m661_freeze_feasibility as freeze  # noqa: E402


def endpoint_receipt() -> dict[str, object]:
    return {
        "checked_at": "2026-10-11T00:00:00Z",
        "source": protocol.ENDPOINT_URL,
        "response_sha256": "1" * 64,
        "model_id": protocol.REQUESTED_MODEL,
        "endpoint": f"{protocol.UPSTREAM} | {protocol.SELECTED_REVISION}",
        "upstream_provider": protocol.UPSTREAM,
        "revision": protocol.SELECTED_REVISION,
        "status": 0,
        "input_usd_per_million_tokens": protocol.INPUT_PRICE_PER_MILLION,
        "output_usd_per_million_tokens": protocol.OUTPUT_PRICE_PER_MILLION,
    }


def test_eight_fresh_seeds_and_balanced_checkpoint_variant_order() -> None:
    excluded, _ = load_exclusion_registry(ROOT)
    seeds = protocol.select_seeds(excluded)
    assert len(seeds) == len(set(seeds)) == 8
    assert not set(seeds) & excluded
    assert seeds == protocol.select_seeds(excluded)
    orders = protocol.checkpoint_orders()
    assert len(orders) == 8
    assert all(set(order) == set(protocol.CHECKPOINTS) for order in orders)
    for checkpoint in protocol.CHECKPOINTS:
        positions = Counter(order.index(checkpoint) for order in orders)
        assert max(positions.values()) - min(positions.values()) <= 1
        variants = [protocol.variant_order(index, checkpoint) for index in range(8)]
        assert variants.count(("vulnerable", "patched")) == 4
        assert variants.count(("patched", "vulnerable")) == 4


def test_plan_is_paired_budgeted_and_unapproved(monkeypatch) -> None:
    monkeypatch.setattr(protocol, "SOURCE_COMMIT", "a" * 40)
    receipt = endpoint_receipt()
    plan = protocol.plan_feasibility(ROOT, protocol_commit="b" * 40, endpoint_receipt=receipt)
    assert plan == protocol.plan_feasibility(
        ROOT, protocol_commit="b" * 40, endpoint_receipt=receipt
    )
    assert plan["planned_cells"] == 96
    assert plan["paid_model_calls_authorized"] is False
    assert plan["excluded_from_confirmatory_analysis"] is True
    assert plan["no_retry_or_replacement"] is True
    assert plan["cumulative_estimated_cost_stop_usd"] == 172.8
    assert plan["analysis_plan"]["unexpected_analysis_exception"] == "abort_global_postcheck"
    cells = plan["cells"]
    assert [cell["order"] for cell in cells] == list(range(1, 97))
    assert len({cell["cell_id"] for cell in cells}) == 96
    assert all(cell["max_total_tokens"] == 120_000 for cell in cells)
    assert Counter(cell["seed"] for cell in cells) == {
        seed: 12 for seed in plan["seed_selection"]["seeds"]
    }
    for seed in plan["seed_selection"]["seeds"]:
        by_seed = [cell for cell in cells if cell["seed"] == seed]
        for checkpoint in protocol.CHECKPOINTS:
            pair = [cell for cell in by_seed if cell["checkpoint_id"] == checkpoint]
            assert len(pair) == 2
            assert {cell["variant"] for cell in pair} == {"vulnerable", "patched"}
            assert len({cell["experiment_sha256"] for cell in pair}) == 2


def test_price_drift_and_unmerged_source_pin_fail_closed(monkeypatch) -> None:
    receipt = endpoint_receipt()
    with pytest.raises(ValueError, match="endpoint or price"):
        protocol._price_snapshot({**receipt, "output_usd_per_million_tokens": 16.0})
    with pytest.raises(ValueError, match="endpoint or price"):
        protocol._price_snapshot({**receipt, "revision": "unexpected"})
    with pytest.raises(ValueError, match="source merge commit"):
        protocol.require_protocol_checkout(ROOT, "b" * 40)
    with pytest.raises(ValueError, match="source merge commit"):
        protocol.plan_feasibility(ROOT, protocol_commit="b" * 40, endpoint_receipt=receipt)


def test_public_endpoint_preflight_requires_exact_revision_and_price(monkeypatch) -> None:
    class Response:
        def __init__(self, raw: bytes):
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return self.raw

    endpoint = {
        "name": f"{protocol.UPSTREAM} | {protocol.SELECTED_REVISION}",
        "provider_name": protocol.UPSTREAM,
        "model_id": protocol.REQUESTED_MODEL,
        "status": 0,
        "pricing": {"prompt": "0.000003", "completion": "0.000015"},
        "supported_parameters": ["tools", "tool_choice", "reasoning_effort"],
    }

    def serve(selected):
        monkeypatch.setattr(
            freeze,
            "urlopen",
            lambda *_args, **_kwargs: Response(
                json.dumps({"data": {"endpoints": [selected]}}).encode()
            ),
        )

    serve(endpoint)
    assert freeze.live_endpoint_receipt()["revision"] == protocol.SELECTED_REVISION
    serve({**endpoint, "pricing": {"prompt": "0.000003", "completion": "0.000016"}})
    with pytest.raises(ValueError, match="drifted"):
        freeze.live_endpoint_receipt()


def test_manifest_freeze_is_create_only(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "experiments" / "manifests" / "m661-feasibility-v1.json"
    destination.parent.mkdir(parents=True)
    monkeypatch.setattr(freeze, "require_protocol_checkout", lambda *_args: None)
    monkeypatch.setattr(freeze, "live_endpoint_receipt", endpoint_receipt)
    monkeypatch.setattr(
        freeze,
        "plan_feasibility",
        lambda *_args, **_kwargs: {
            "planned_cells": 96,
            "cumulative_estimated_cost_stop_usd": 172.8,
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "m661_freeze_feasibility.py",
            str(destination),
            "--repository-root",
            str(tmp_path),
            "--protocol-commit",
            "b" * 40,
        ],
    )
    freeze.main()
    original = destination.read_bytes()
    with pytest.raises(FileExistsError):
        freeze.main()
    assert destination.read_bytes() == original
