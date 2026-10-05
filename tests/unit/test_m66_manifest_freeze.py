"""The excluded pilot matrix is fixed without compiling its selected fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from offsecgym.research import m66_pilot_protocol as protocol
from offsecgym.runtime.enterprise import FAMILY as ENTERPRISE_FAMILY

ROOT = Path(__file__).resolve().parents[2]


def _source_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def test_pilot_manifest_is_exactly_eight_balanced_excluded_cells() -> None:
    manifest = protocol.plan_pilot(
        ROOT, source_commit=_source_commit(), price_checked_at="2026-10-05T20:05:00Z"
    )
    cells = manifest["cells"]
    assert len(cells) == len({cell["cell_id"] for cell in cells}) == 8
    assert manifest["seed_selection"]["excluded_from_confirmatory_sample"] is True
    assert manifest["seed_selection"]["fixture_inspection_before_selection"] is False
    assert manifest["paid_model_calls_authorized"] is False
    assert manifest["maximum_configured_estimated_cost_usd"] == 14.4
    assert {cell["seed"] for cell in cells} == {124501, 704929}
    assert {cell["range_family"] for cell in cells} == {"saas", ENTERPRISE_FAMILY}
    assert [(cell["arm"], cell["arm_order"]) for cell in cells] == [
        ("witness", ["witness", "control"]),
        ("control", ["witness", "control"]),
        ("control", ["control", "witness"]),
        ("witness", ["control", "witness"]),
        ("control", ["control", "witness"]),
        ("witness", ["control", "witness"]),
        ("witness", ["witness", "control"]),
        ("control", ["witness", "control"]),
    ]
    for family in ("saas", ENTERPRISE_FAMILY):
        for variant in ("vulnerable", "patched"):
            pair = [
                cell
                for cell in cells
                if cell["range_family"] == family and cell["variant"] == variant
            ]
            assert len(pair) == 2
            for field in (
                "seed",
                "experiment_sha256",
                "max_total_tokens",
                "max_model_calls",
                "max_actions",
                "max_http_requests",
            ):
                assert pair[0][field] == pair[1][field]
    for name in (
        "model_policy",
        "range_surface",
        "witness_policy",
        "pair_runner",
    ):
        assert manifest[f"{name}_sha256"] == manifest["source_hashes"][name]["sha256"]
    assert manifest["trace_analysis_sha256"] == manifest["trace_analysis"]["sha256"]


def test_pilot_manifest_rejects_source_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    original = protocol._source_file

    def changed(root: Path, commit: str, path: str) -> bytes:
        if path == "src/offsecgym/solver/monolithic.py":
            return b"changed policy"
        return original(root, commit, path)

    monkeypatch.setattr(protocol, "_source_file", changed)
    with pytest.raises(ValueError, match="model_policy differs from pinned source commit"):
        protocol.plan_pilot(
            ROOT, source_commit=_source_commit(), price_checked_at="2026-10-05T20:05:00Z"
        )


def test_pilot_manifest_rejects_unusable_price_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        protocol.plan_pilot(
            ROOT, source_commit=_source_commit(), price_checked_at="2026-10-05T20:05:00"
        )
