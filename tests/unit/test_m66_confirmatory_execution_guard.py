"""A frozen assignment cannot become paid work through a stale approval."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m66_confirmatory_collect as collector  # noqa: E402
from m66_confirmatory_analyze import analyze  # noqa: E402
from m66_confirmatory_audit import _postterminal_bookkeeping_only  # noqa: E402
from m66_confirmatory_collect import _execution_approval, _spec  # noqa: E402
from m66_confirmatory_postcheck import _journal_records  # noqa: E402

from offsecgym.research.m66_confirmatory_protocol import plan_confirmatory  # noqa: E402
from offsecgym.schemas.events import (  # noqa: E402
    CoverageLeaseReleased,
    CoverageUpdated,
    RunCompleted,
    RunStarted,
)


def _manifest():
    return plan_confirmatory(
        ROOT,
        protocol_commit="a" * 40,
        endpoint_receipt={
            "checked_at": "2026-10-07T00:00:00Z",
            "source": "https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints",
            "model_id": "moonshotai/kimi-k3",
            "endpoint": "Moonshot AI | moonshotai/kimi-k3-20260715",
            "upstream_provider": "Moonshot AI",
            "revision": "moonshotai/kimi-k3-20260715",
            "status": 0,
            "input_usd_per_million_tokens": 3.0,
            "output_usd_per_million_tokens": 15.0,
        },
    )


def test_paid_collection_requires_exact_post_freeze_approval(monkeypatch) -> None:
    assert collector.EXPECTED_MANIFEST_SHA256 == (
        "d0f9f516d93008407196165296c5e8ce7d0c4a7fbcc2aed02c14eec3150f61f4"
    )
    with pytest.raises(ValueError, match="approval artifact hash differs"):
        _execution_approval(_manifest(), b"{}")

    approval = {
        "protocol": "m66-confirmatory-v1",
        "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "source_commit": collector.EXPECTED_SOURCE_COMMIT,
        "protocol_commit": "a" * 40,
        "approved_at": "2026-10-07T01:00:00Z",
        "cost_ceiling_usd": 504.0,
        "approval_scope": "m66_confirmatory_280_cells_only",
        "approved_for_paid_calls": True,
    }
    raw = json.dumps(approval, sort_keys=True).encode()
    monkeypatch.setattr(collector, "EXPECTED_APPROVAL_SHA256", hashlib.sha256(raw).hexdigest())
    assert _execution_approval(_manifest(), raw) == approval
    with pytest.raises(ValueError, match="approval does not authorize"):
        changed = {**approval, "cost_ceiling_usd": 505.0}
        changed_raw = json.dumps(changed, sort_keys=True).encode()
        monkeypatch.setattr(
            collector, "EXPECTED_APPROVAL_SHA256", hashlib.sha256(changed_raw).hexdigest()
        )
        _execution_approval(_manifest(), changed_raw)
    health = collector._health_module(ROOT)
    assert (
        health.provider_health((health.CellTerminal("provider_failed", "provider_unavailable"),))
        == "continue"
    )


def test_derived_cell_spec_matches_each_frozen_family_and_seed() -> None:
    manifest = _manifest()
    for family in ("saas", "enterprise_change_control_v1"):
        cell = next(item for item in manifest["cells"] if item["range_family"] == family)
        spec = _spec(ROOT, manifest, cell)
        assert spec.seed == spec.range.seed == cell["seed"]
        assert spec.range.patched == (cell["variant"] == "patched")


def test_journal_must_be_contiguous_frozen_prefix() -> None:
    manifest = _manifest()
    cells = manifest["cells"]
    header = {
        "type": "batch_started",
        "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "expected_model_revision": manifest["expected_selected_endpoint"]["revision"],
        "expected_upstream_provider": "Moonshot AI",
        "max_estimated_usd": 504.0,
    }
    started = {"type": "cell_started", "cell_id": cells[0]["cell_id"], "order": 1}
    ended = {
        "type": "cell_completed",
        "cell_id": cells[0]["cell_id"],
        "order": 1,
        "run_id": "run-a",
    }

    def data(rows):
        return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()

    assert _journal_records(manifest, data([header, started, ended])) == [ended]
    with pytest.raises(ValueError, match="interrupted"):
        _journal_records(manifest, data([header, started]))
    with pytest.raises(ValueError, match="frozen 280-cell order"):
        _journal_records(manifest, data([header, started, {**ended, "order": 2}]))


def test_all_unstarted_assignment_remains_in_analysis(tmp_path, monkeypatch) -> None:
    import hashlib

    manifest = _manifest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    journal_path = tmp_path / "journal.jsonl"
    journal_path.write_text(json.dumps({"manifest_sha256": manifest_sha}) + "\n")
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "manifest_sha256": manifest_sha,
                "journal_sha256": hashlib.sha256(journal_path.read_bytes()).hexdigest(),
                "cells": {},
            }
        )
    )
    monkeypatch.setattr(
        "m66_confirmatory_analyze.subprocess.check_output", lambda *_, **__: "a" * 40
    )
    result = analyze(manifest_path, journal_path, audit_path)
    assert result["observed_cells"] == 0
    assert result["unstarted_cells"] == 280
    assert result["results"]["primary_by_family"]["saas"]["assigned_root_pairs"] == 60
    assert result["results"]["primary_by_family"]["saas"]["difference_per_assigned_root"] is None


def _request():
    return {
        "model": "moonshotai/kimi-k3",
        "provider": {"order": ["moonshotai"], "allow_fallbacks": False},
        "reasoning": {"effort": "high"},
        "store": False,
    }


@pytest.mark.asyncio
async def test_isolated_transport_failure_does_not_close_partner_route(monkeypatch) -> None:
    async def unavailable(_self, _request):
        raise collector.ProviderFailure("provider_unavailable")

    monkeypatch.setattr(collector.OpenRouterResponsesProvider, "complete", unavailable)
    guard = collector.EndpointGuard(("moonshotai/kimi-k3-20260715", "Moonshot AI"))
    with pytest.raises(collector.ProviderFailure, match="provider_unavailable"):
        await guard.provider("test-key").complete(_request())
    assert guard.stopped is False


@pytest.mark.asyncio
async def test_endpoint_drift_closes_confirmatory_route(monkeypatch) -> None:
    async def wrong_endpoint(_self, _request):
        return SimpleNamespace(
            raw_response={"openrouter_metadata": {"endpoints": {"available": []}}}
        )

    monkeypatch.setattr(collector.OpenRouterResponsesProvider, "complete", wrong_endpoint)
    guard = collector.EndpointGuard(("moonshotai/kimi-k3-20260715", "Moonshot AI"))
    with pytest.raises(collector.ProviderFailure, match="confirmatory_endpoint_drift"):
        await guard.provider("test-key").complete(_request())
    assert guard.stopped is True


def test_only_coverage_closure_may_follow_run_terminal() -> None:
    run_id, claim_id, task_id = uuid4(), uuid4(), uuid4()
    terminal = RunCompleted(run_id=run_id, sequence_number=2, actor="test", status="completed")
    closure = [
        CoverageUpdated(
            run_id=run_id,
            sequence_number=3,
            actor="worldstate",
            claim_id=claim_id,
            status="released",
        ),
        CoverageLeaseReleased(
            run_id=run_id,
            sequence_number=4,
            actor="worldstate",
            claim_id=claim_id,
            task_id=task_id,
            status="released",
        ),
    ]
    assert _postterminal_bookkeeping_only([terminal, *closure], terminal.sequence_number)
    later_work = RunStarted(
        run_id=run_id, sequence_number=5, actor="test", experiment_hash="unexpected"
    )
    assert not _postterminal_bookkeeping_only(
        [terminal, *closure, later_work], terminal.sequence_number
    )
