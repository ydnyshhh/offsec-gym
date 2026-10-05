"""Frozen M6.5.1 pairs, pins, and cost gates are checked before live calls."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from offsecgym.research import m65_witness_execute
from offsecgym.research.m65_witness_execute import execute, spec_for_cell, verify_manifest
from offsecgym.research.m65_witness_matrix import PILOT_PROTOCOL, PROTOCOL

ROOT = Path(__file__).parents[2]
MAIN = ROOT / "experiments/manifests/m651-witness-recovery-v2.json"
PILOT = ROOT / "experiments/manifests/m651-witness-recovery-pilot-v2.json"
ORIGINAL_PILOT = ROOT / "experiments/manifests/m651-witness-recovery-pilot-v1.json"


def test_frozen_sample_is_new_paired_and_pinned_to_historical_source() -> None:
    sample = json.loads(MAIN.read_text())
    pilot = json.loads(PILOT.read_text())
    for manifest in (sample, pilot):
        for path, digest in manifest["source_files"].items():
            committed = subprocess.run(
                ["git", "show", f"{manifest['source_commit']}:{path}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout
            assert hashlib.sha256(committed).hexdigest() == digest
    # The historical collector must refuse to run on a later implementation.
    with pytest.raises(ValueError, match="no longer rebuilds|source differs"):
        verify_manifest(ROOT, MAIN)
    assert sample["protocol"] == PROTOCOL
    assert pilot["protocol"] == PILOT_PROTOCOL
    assert len(sample["seed_set"]) == 10
    assert len(sample["cells"]) == 20
    assert len(pilot["cells"]) == 1
    assert pilot["cells"][0]["variant"] == "vulnerable"
    assert (
        pilot["amendment_of"]["manifest_sha256"]
        == hashlib.sha256(ORIGINAL_PILOT.read_bytes()).hexdigest()
    )
    assert pilot["seed_set"] == [sample["pilot_seed"]]
    assert not set(sample["seed_set"]) & set(range(1001, 1011))
    assert sample["pilot_seed"] not in sample["seed_set"]
    assert sample["combined_model_budget"]["max_total_tokens"] == 200_000
    assert sample["probe_budget"]["max_total_tokens"] == 120_000
    assert sample["reporter_budget"]["max_total_tokens"] == 80_000
    assert sample["maximum_estimated_token_cost_usd"] == 44.73216
    assert sample["cumulative_estimated_cost_stop_usd"] == 45.0
    assert pilot["maximum_estimated_token_cost_usd"] == 2.236608
    assert [x["order"] for x in sample["cells"]] == list(range(1, 21))
    assert len({x["cell_id"] for x in sample["cells"]}) == 20
    for seed in sample["seed_set"]:
        cells = [x for x in sample["cells"] if x["range_seed"] == seed]
        assert {x["variant"] for x in cells} == {"vulnerable", "patched"}
        assert len({x["pair_id"] for x in cells}) == 1
        assert len({x["build_id"] for x in cells}) == 2


def test_cell_rejects_seed_or_config_drift() -> None:
    manifest = json.loads(MAIN.read_text())
    cell = manifest["cells"][0]
    with pytest.raises(ValueError, match="outside frozen"):
        spec_for_cell(ROOT, manifest, {**cell, "range_seed": 1101})
    altered = {**manifest, "probe_config": {**manifest["probe_config"], "sha256": "0" * 64}}
    with pytest.raises(ValueError, match="config hash"):
        spec_for_cell(ROOT, altered, cell)


@pytest.mark.asyncio
async def test_cost_ceiling_stops_before_credentials_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        m65_witness_execute,
        "verify_manifest",
        lambda root, path: json.loads(path.read_text()),
    )
    with pytest.raises(ValueError, match="cost limit"):
        await execute(
            ROOT,
            PILOT,
            tmp_path / "journal.jsonl",
            tmp_path / "state",
            max_estimated_usd=2.236609,
            max_cells=0,
        )
    assert not (tmp_path / "journal.jsonl").exists()
