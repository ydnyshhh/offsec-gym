"""The M6.5 control collector rejects drift and over-budget launches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from offsecgym.research.m65_monolithic_execute import execute, spec_for_cell, verify_manifest


def _manifest_path() -> Path:
    return (
        Path(__file__).parents[2] / "experiments/manifests/m65-common-bootstrap-monolithic-v1.json"
    )


def test_m65_collector_reconstructs_pinned_control_cells(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    manifest = verify_manifest(root, _manifest_path(), require_source_history=False)
    assert manifest["planned_live_cells"] == 100
    for cell in manifest["cells"]:
        spec = spec_for_cell(root, manifest, cell)
        assert spec.budget.max_total_tokens == cell["model_token_budget"]
        assert spec.range.patched == (cell["variant"] == "patched")
    changed = {**manifest, "cells": list(manifest["cells"])}
    changed["cells"][0] = {**changed["cells"][0], "experiment_sha256": "0" * 64}
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="no longer rebuilds"):
        verify_manifest(root, path, require_source_history=False)
    with pytest.raises(ValueError, match="experiment hash"):
        spec_for_cell(root, manifest, changed["cells"][0])


@pytest.mark.asyncio
async def test_m65_collector_checks_cost_and_cell_limit_before_provider(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    args = (
        root,
        _manifest_path(),
        tmp_path / "journal.jsonl",
        tmp_path / "historical.jsonl",
        tmp_path / "state",
    )
    with pytest.raises(ValueError, match="reviewed bound"):
        await execute(*args, max_estimated_usd=139.0)
    with pytest.raises(ValueError, match="max_cells"):
        await execute(*args, max_estimated_usd=1.0, max_cells=-1)
