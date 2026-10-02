"""The approved collector reconstructs exact cells and refuses ambiguous resumes."""

import json
from pathlib import Path

import pytest

from offsecgym.experiment.scripted import experiment_hash
from offsecgym.research.m64_execute import Journal, spec_for_cell, verify_manifest


def test_m64_collector_reconstructs_every_feasible_cell() -> None:
    root = Path(__file__).parents[2]
    manifest = verify_manifest(
        root,
        root / "experiments/manifests/m64-v2-worker-primary-1.json",
        require_source_history=False,  # GitHub Actions checks out only the tip commit.
    )
    for cell in manifest["cells"]:
        if cell["policy_feasible"]:
            spec = spec_for_cell(root, manifest, cell)
            assert experiment_hash(spec) == cell["experiment_sha256"]
            assert spec.range.seed == cell["range_seed"]
            assert spec.range.patched == (cell["variant"] == "patched")
            assert spec.budget.max_total_tokens == cell["worker_token_budget"]


def test_m64_journal_fails_closed_on_interrupted_cell(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    kwargs = {
        "manifest_sha256": "a" * 64,
        "expected_endpoint": ("moonshotai/kimi-k3-20260715", "Moonshot AI"),
        "max_estimated_usd": 306.0,
    }
    first = Journal(path, **kwargs)
    first.append({"type": "cell_started", "cell_id": "cell-1", "order": 1})
    with pytest.raises(ValueError, match="another M6.4 collector"):
        Journal(path, **kwargs)
    first.close()
    with pytest.raises(ValueError, match="interrupted cell"):
        Journal(path, **kwargs)
    assert json.loads(path.read_text().splitlines()[0])["max_estimated_usd"] == 306.0


def test_m64_journal_resumes_only_completed_cells(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    kwargs = {
        "manifest_sha256": "a" * 64,
        "expected_endpoint": ("moonshotai/kimi-k3-20260715", "Moonshot AI"),
        "max_estimated_usd": 306.0,
    }
    first = Journal(path, **kwargs)
    first.append({"type": "cell_started", "cell_id": "cell-1", "order": 1})
    first.append({"type": "cell_completed", "cell_id": "cell-1", "estimated_cost_usd": 0.1})
    first.close()
    resumed = Journal(path, **kwargs)
    assert set(resumed.completed) == {"cell-1"}
    resumed.close()
    with pytest.raises(ValueError, match="header"):
        Journal(path, **{**kwargs, "max_estimated_usd": 400.0})
