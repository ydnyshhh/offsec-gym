"""No-model checks for the closed M6.7.1 paid collector gate."""

from __future__ import annotations

import hashlib
import json
import sys
from argparse import Namespace
from decimal import Decimal
from pathlib import Path

import pytest

from offsecgym.providers.base import ProviderRequestError

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m671_collect as collector  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / collector.MANIFEST_PATH


def test_live_manifest_exact_scope_and_closed_pin() -> None:
    raw = MANIFEST_PATH.read_bytes()
    manifest = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == collector.MANIFEST_SHA256
    assert manifest["protocol_commit"] == collector.PROTOCOL_COMMIT
    assert manifest["planned_cells"] == 128
    assert manifest["planned_pairs"] == 64
    assert manifest["paid_model_calls_authorized"] is False
    assert Decimal(manifest["cumulative_estimated_cost_stop_usd"]) == Decimal("230.4")
    assert collector.CELL_CEILING == Decimal("1.8")
    assert collector.APPROVAL_SHA256 == "0" * 64
    with pytest.raises(ValueError, match="closed"):
        collector.private_approval(ROOT / "no-private-approval.json", raw)


def test_every_live_cell_spec_replays_before_provider_admission() -> None:
    manifest = json.loads(MANIFEST_PATH.read_bytes())
    for cell in manifest["cells"]:
        spec = collector.cell_spec(ROOT, cell)
        assert spec.seed == cell["seed"]
        assert spec.range.patched == (cell["variant"] == "patched")
    changed = {**manifest["cells"][0], "experiment_sha256": "0" * 64}
    with pytest.raises(ValueError, match="frozen assignment"):
        collector.cell_spec(ROOT, changed)


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        (["provider_unavailable"], "continue"),
        (["provider_unavailable", "provider_timeout"], "pause"),
        (["provider_rate_limited", "provider_rate_limited"], "continue"),
        (["provider_rate_limited"] * 3, "pause"),
        (["provider_auth_failed"], "stop"),
    ],
)
def test_provider_health_gate(reasons: list[str], expected: str) -> None:
    records = [
        {"terminal_status": "provider_failed", "failure_reason": reason} for reason in reasons
    ]
    assert collector.health_decision(records) == expected


def test_exclusive_journal_and_frozen_prefix(tmp_path: Path) -> None:
    from offsecgym.research.m671_postcheck import journal_prefix

    manifest = json.loads(MANIFEST_PATH.read_bytes())
    path = tmp_path / "journal.jsonl"
    journal = collector.Journal(path, collector.MANIFEST_SHA256)
    cell = manifest["cells"][0]
    journal.append(
        {
            "type": "cell_started",
            "cell_id": cell["cell_id"],
            "order": 1,
            "run_id": "00000000-0000-0000-0000-000000000001",
        }
    )
    journal.close()
    assert (
        journal_prefix(manifest, path.read_bytes(), manifest_sha256=collector.MANIFEST_SHA256)[0][
            "type"
        ]
        == "interrupted"
    )
    with pytest.raises(FileExistsError):
        collector.Journal(path, collector.MANIFEST_SHA256)


@pytest.mark.asyncio
async def test_provider_guard_rejects_policy_drift_before_network() -> None:
    guard = collector.EndpointGuard()
    provider = guard.provider("unused-test-key")
    with pytest.raises(ProviderRequestError, match="policy_drift"):
        await provider.complete(
            {
                "model": collector.MODEL,
                "provider": {"order": ["moonshotai"], "allow_fallbacks": True},
                "reasoning": {"effort": "high"},
                "store": False,
            }
        )
    assert guard.stopped


@pytest.mark.asyncio
async def test_symlinked_study_artifact_refused_before_admission(tmp_path: Path) -> None:
    linked = tmp_path / "manifest.json"
    linked.symlink_to(MANIFEST_PATH)
    args = Namespace(
        execution_root=ROOT,
        source_root=ROOT,
        protocol_root=ROOT,
        manifest=linked,
        approval=tmp_path / "approval.json",
        journal=tmp_path / "journal.jsonl",
        state_root=tmp_path / "range-state",
        pair_directory=tmp_path / "pairs",
    )
    with pytest.raises(ValueError, match="symlink"):
        await collector.collect(args)
