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
from offsecgym.schemas.events import (
    ActionCompleted,
    FindingSubmitted,
    FindingValidated,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
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
    variants = (
        base,
        base.model_copy(update={"patched": True}),
        base.model_copy(update={"patched_properties": ("DOC-CROSS-TENANT-READ",)}),
    )
    for variant in variants:
        spec = ExperimentSpec(
            name="scripted_saas",
            seed=1,
            range=variant,
            budget=Budget(max_actions=30, max_http_requests=30, max_wall_seconds=240),
            orchestrator="scripted",
            validation="deterministic",
        )
        outcomes.append(await runner.run(spec))
    vulnerable, patched, selective = outcomes
    assert len({item.build_id for item in outcomes}) == 3
    assert all(item.evaluation.score_valid for item in outcomes)
    assert vulnerable.evaluation.true_positives == 5
    assert vulnerable.evaluation.false_positives == vulnerable.evaluation.false_negatives == 0
    assert vulnerable.evaluation.precision == vulnerable.evaluation.recall == 1.0
    assert len(vulnerable.findings) == len(vulnerable.validations) == 5
    assert all(result.status == "validated" for result in vulnerable.validations)
    assert len([result for result in vulnerable.validations if result.replay_evidence_ids]) == 1
    refund_result = next(item for item in vulnerable.validations if item.replay_trace is not None)
    replay_ref = refund_result.replay_trace
    assert replay_ref is not None
    assert replay_ref.evidence_ids == refund_result.replay_evidence_ids
    replay_events = await events.read_run(replay_ref.replay_run_id)
    assert isinstance(replay_events[0], RunStarted)
    assert isinstance(replay_events[1], RangeStarted)
    assert replay_events[1].range_instance_id == replay_ref.range_instance_id
    assert replay_events[1].range_generation == replay_ref.range_generation
    assert len([event for event in replay_events if isinstance(event, ActionCompleted)]) == 3
    assert isinstance(replay_events[-1], RunCompleted)
    assert replay_events[-1].status == "completed"
    assert patched.findings == patched.validations == ()
    assert patched.evaluation.true_positives == patched.evaluation.false_positives == 0
    assert patched.evaluation.false_negatives == 0
    assert selective.evaluation.true_positives == 4
    assert selective.evaluation.false_positives == selective.evaluation.false_negatives == 0
    assert len(selective.findings) == 4
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


@pytest.mark.docker
@pytest.mark.parametrize("seed", range(1001, 1011))
async def test_v2_held_out_scripted_pair(tmp_path: Path, seed: int) -> None:
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
    ).model_copy(update={"scenario": "tenant_boundary_v2", "seed": seed})
    outcomes = []
    for variant in (base, base.model_copy(update={"patched": True})):
        outcomes.append(
            await runner.run(
                ExperimentSpec(
                    name=f"v2_scripted_pair_{seed}",
                    seed=1,
                    range=variant,
                    budget=Budget(max_actions=30, max_http_requests=30, max_wall_seconds=240),
                    orchestrator="scripted",
                    validation="deterministic",
                )
            )
        )
    vulnerable, patched = outcomes
    assert vulnerable.build_id != patched.build_id
    assert (
        runtime.state.load_build(vulnerable.build_id).pair_id
        == runtime.state.load_build(patched.build_id).pair_id
    )
    assert vulnerable.evaluation.score_valid and patched.evaluation.score_valid
    assert vulnerable.evaluation.true_positives == 5
    assert vulnerable.evaluation.false_positives == vulnerable.evaluation.false_negatives == 0
    assert patched.evaluation.true_positives == patched.evaluation.false_positives == 0
    assert patched.evaluation.false_negatives == 0
    assert len(vulnerable.findings) == len(vulnerable.validations) == 5
    assert patched.findings == patched.validations == ()
