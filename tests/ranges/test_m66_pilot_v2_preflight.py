"""Selected Range B v2 pilot fixture finishes bootstrap in both model arms."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from test_enterprise_experiment import MemoryEvents
from test_enterprise_model_arms import OneReadProvider

from offsecgym.research.m66_pair import WitnessPlanningPairRunner
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import PrerequisiteBootstrapCompleted
from offsecgym.schemas.specs import ExperimentSpec

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.docker
@pytest.mark.parametrize("patched", (False, True))
async def test_selected_v2_range_b_bootstrap_finishes(tmp_path: Path, patched: bool) -> None:
    available = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if available.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required by CI: {available.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")
    base = ExperimentSpec.model_validate(
        yaml.safe_load((ROOT / "experiments/configs/m66-pilot-v2-range-b.yaml").read_text())
    )
    spec = base.model_copy(update={"range": base.range.model_copy(update={"patched": patched})})
    events = MemoryEvents()
    pair = await WitnessPlanningPairRunner(
        ComposeRangeRuntime(tmp_path), events, OneReadProvider
    ).run_pair(spec)
    completed = [
        event for event in events.items if isinstance(event, PrerequisiteBootstrapCompleted)
    ]
    assert {event.run_id for event in completed} == {pair.control.run_id, pair.witness.run_id}
    assert [event.action_count for event in completed] == [33, 33]
    assert all(event.http_request_count == 33 for event in completed)
    assert pair.control.evaluation.score_valid
    assert pair.witness.evaluation.score_valid
