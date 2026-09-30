"""Infrastructure failures stay visible and do not become agent false negatives."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
import yaml

from offsecgym.experiment import ScriptedExperimentRunner
from offsecgym.runtime.compose import DockerCommandError
from offsecgym.schemas.events import RunCompleted
from offsecgym.schemas.specs import ExperimentSpec


class MemoryEvents:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        stored = event.model_copy(update={"sequence_number": len(self.items) + 1})
        self.items.append(stored)
        return stored

    async def read_run(self, run_id: UUID):
        return [item for item in self.items if item.run_id == run_id]


class FailedRuntime:
    async def build(self, _):
        raise DockerCommandError("synthetic Docker failure")


@pytest.mark.asyncio
async def test_runtime_failure_is_recorded_without_agent_score() -> None:
    spec = ExperimentSpec.model_validate(
        yaml.safe_load(
            (
                Path(__file__).parents[2] / "experiments" / "configs" / "scripted-saas.yaml"
            ).read_text()
        )
    )
    events = MemoryEvents()
    outcome = await ScriptedExperimentRunner(FailedRuntime(), events).run(spec)
    assert outcome.evaluation.status == "environment_failed"
    assert outcome.evaluation.precision is outcome.evaluation.recall is None
    assert outcome.evaluation.false_positives == outcome.evaluation.false_negatives == 0
    assert outcome.failure_reason == "DockerCommandError"
    assert isinstance(events.items[-1], RunCompleted)
    assert events.items[-1].status == "environment_failed"
