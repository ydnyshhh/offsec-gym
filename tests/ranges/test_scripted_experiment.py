"""A real range proves the scripted agent, evidence, validator, replay, and score path."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from offsecgym.experiment import ScriptedExperimentRunner
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import FindingSubmitted, FindingValidated, RunCompleted
from offsecgym.schemas.specs import Budget, ExperimentSpec, RangeSpec


class MemoryEvents:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        sequence = sum(item.run_id == event.run_id for item in self.items) + 1
        stored = event.model_copy(update={"sequence_number": sequence})
        self.items.append(stored)
        return stored

    async def read_run(self, run_id: UUID):
        return [item for item in self.items if item.run_id == run_id]


@pytest.mark.docker
async def test_scripted_vulnerable_and_patched_pair(tmp_path: Path) -> None:
    result = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required by CI: {result.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")
    runtime = ComposeRangeRuntime(tmp_path)
    events = MemoryEvents()
    runner = ScriptedExperimentRunner(runtime, events)
    base = RangeSpec.model_validate(
        yaml.safe_load((Path(__file__).parents[2] / "examples" / "saas-range.yaml").read_text())
    )
    outcomes = []
    for patched in (False, True):
        spec = ExperimentSpec(
            name="scripted_saas",
            seed=1,
            range=base.model_copy(update={"patched": patched}),
            budget=Budget(max_actions=30, max_http_requests=30, max_wall_seconds=240),
            orchestrator="scripted",
            validation="deterministic",
        )
        outcomes.append(await runner.run(spec))
    vulnerable, patched = outcomes
    assert vulnerable.build_id != patched.build_id
    assert vulnerable.evaluation.true_positives == 5
    assert vulnerable.evaluation.false_positives == vulnerable.evaluation.false_negatives == 0
    assert vulnerable.evaluation.precision == vulnerable.evaluation.recall == 1.0
    assert len(vulnerable.findings) == len(vulnerable.validations) == 5
    assert all(result.status == "validated" for result in vulnerable.validations)
    assert len([result for result in vulnerable.validations if result.replay_evidence_ids]) == 1
    assert patched.findings == patched.validations == ()
    assert patched.evaluation.true_positives == patched.evaluation.false_positives == 0
    assert patched.evaluation.false_negatives == 0
    for outcome in outcomes:
        trace = await events.read_run(outcome.run_id)
        assert len([event for event in trace if isinstance(event, FindingSubmitted)]) == len(
            outcome.findings
        )
        assert len([event for event in trace if isinstance(event, FindingValidated)]) == len(
            outcome.validations
        )
        assert isinstance(trace[-1], RunCompleted)
        assert trace[-1].status == "completed"
