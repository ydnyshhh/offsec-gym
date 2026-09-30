"""Infrastructure failures stay visible and do not become agent false negatives."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from offsecgym.experiment import ScriptedExperimentRunner
from offsecgym.experiment.scripted import experiment_hash
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError
from offsecgym.runtime.manifests import utc_now
from offsecgym.schemas.domain import (
    AgentResult,
    CandidateFinding,
    EvidenceRef,
    RangeControllerMetadata,
    RangeInstanceStatus,
    ValidationContext,
    ValidationResult,
)
from offsecgym.schemas.events import FindingSubmitted, RunCompleted
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


def scripted_spec() -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        yaml.safe_load(
            (
                Path(__file__).parents[2] / "experiments" / "configs" / "scripted-saas.yaml"
            ).read_text()
        )
    )


def no_docker_runtime(tmp_path: Path, monkeypatch) -> ComposeRangeRuntime:
    runtime = ComposeRangeRuntime(tmp_path)

    async def status(instance_id):
        instance = runtime.state.load_instance(instance_id)
        return RangeInstanceStatus(
            instance_id=instance_id,
            build_id=instance.build_id,
            generation=instance.generation,
            state="healthy",
            checked_at=utc_now(),
        )

    async def metadata(instance_id):
        instance = runtime.state.load_instance(instance_id)
        build = runtime.state.verify_build_integrity(instance.build_id)
        return RangeControllerMetadata(
            instance_id=instance_id,
            build_id=instance.build_id,
            generation=instance.generation,
            project_name=instance.project_name,
            spec_sha256=build.spec_sha256,
            seed=build.spec.seed,
            state="healthy",
            family="saas",
            security_variant="vulnerable",
        )

    async def destroy(instance_id):
        return await status(instance_id)

    monkeypatch.setattr(runtime, "start_instance", status)
    monkeypatch.setattr(runtime, "snapshot_metadata", metadata)
    monkeypatch.setattr(runtime, "destroy_instance", destroy)
    return runtime


def test_experiment_hash_ignores_mapping_insertion_order() -> None:
    spec = scripted_spec()
    reversed_identities = dict(reversed(list(spec.range.identities.items())))
    reordered = spec.model_copy(
        update={"range": spec.range.model_copy(update={"identities": reversed_identities})}
    )
    assert experiment_hash(spec) == experiment_hash(reordered)


@pytest.mark.asyncio
async def test_runtime_failure_is_recorded_without_agent_score() -> None:
    spec = scripted_spec()
    events = MemoryEvents()
    outcome = await ScriptedExperimentRunner(FailedRuntime(), events).run(spec)
    assert outcome.evaluation.status == "environment_failed"
    assert outcome.evaluation.score_valid is False
    assert outcome.evaluation.precision is outcome.evaluation.recall is None
    assert outcome.evaluation.false_positives is outcome.evaluation.false_negatives is None
    assert outcome.failure_reason == "DockerCommandError"
    assert isinstance(events.items[-1], RunCompleted)
    assert events.items[-1].status == "environment_failed"


@pytest.mark.parametrize(
    ("agent_behavior", "expected_status"),
    (
        ("failed", "agent_failed"),
        ("budget_exhausted", "budget_exhausted"),
        ("timeout", "budget_exhausted"),
        ("exception", "agent_failed"),
        ("action_budget", "budget_exhausted"),
    ),
)
@pytest.mark.asyncio
async def test_agent_outcomes_are_scored(
    tmp_path: Path, monkeypatch, agent_behavior: str, expected_status: str
) -> None:
    from offsecgym.solver.scripted import ScriptedSaasSolver

    async def agent_run(self, task, context, tools):
        if agent_behavior == "action_budget":
            from offsecgym.solver.scripted import AgentBudgetExhausted

            raise AgentBudgetExhausted("action_budget_exhausted")
        if agent_behavior == "timeout":
            raise TimeoutError("synthetic agent wall-time exhaustion")
        if agent_behavior == "exception":
            raise ValueError("synthetic agent error")
        return AgentResult(task_id=task.task_id, status=agent_behavior)

    monkeypatch.setattr(ScriptedSaasSolver, "run", agent_run)
    events = MemoryEvents()
    outcome = await ScriptedExperimentRunner(no_docker_runtime(tmp_path, monkeypatch), events).run(
        scripted_spec()
    )
    assert outcome.evaluation.status == expected_status
    assert outcome.evaluation.score_valid is True
    assert outcome.evaluation.true_positives == 0
    assert outcome.evaluation.false_negatives == 5
    assert outcome.evaluation.recall == 0.0
    terminal = [event for event in events.items if isinstance(event, RunCompleted)]
    assert len(terminal) == 1
    assert terminal[0].status == expected_status


@pytest.mark.asyncio
async def test_evaluator_exception_emits_validation_failed_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    import offsecgym.experiment.scripted as scripted
    from offsecgym.solver.scripted import ScriptedSaasSolver

    async def agent_run(self, task, context, tools):
        return AgentResult(task_id=task.task_id, status="completed")

    def broken_evaluator(*args, **kwargs):
        raise ValueError("synthetic evaluator mismatch")

    monkeypatch.setattr(ScriptedSaasSolver, "run", agent_run)
    monkeypatch.setattr(scripted, "evaluate_run", broken_evaluator)
    events = MemoryEvents()
    outcome = await ScriptedExperimentRunner(no_docker_runtime(tmp_path, monkeypatch), events).run(
        scripted_spec()
    )
    assert outcome.evaluation.status == "validation_failed"
    assert outcome.evaluation.score_valid is False
    assert outcome.evaluation.false_negatives is None
    terminal = [event for event in events.items if isinstance(event, RunCompleted)]
    assert len(terminal) == 1 and terminal[0].status == "validation_failed"


@pytest.mark.asyncio
async def test_controller_cancellation_emits_terminal_event(tmp_path: Path, monkeypatch) -> None:
    from offsecgym.solver.scripted import ScriptedSaasSolver

    async def agent_run(self, task, context, tools):
        raise asyncio.CancelledError

    monkeypatch.setattr(ScriptedSaasSolver, "run", agent_run)
    events = MemoryEvents()
    with pytest.raises(asyncio.CancelledError):
        await ScriptedExperimentRunner(no_docker_runtime(tmp_path, monkeypatch), events).run(
            scripted_spec()
        )
    terminal = [event for event in events.items if isinstance(event, RunCompleted)]
    assert len(terminal) == 1 and terminal[0].status == "cancelled"


@pytest.mark.asyncio
async def test_timeout_scores_findings_submitted_before_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    from offsecgym.runtime.oracle import StateOracleStore
    from offsecgym.solver.scripted import ScriptedSaasSolver
    from offsecgym.validation import DeterministicValidator

    runtime = no_docker_runtime(tmp_path, monkeypatch)
    events = MemoryEvents()
    submitted: list[CandidateFinding] = []

    async def agent_run(self, task, context, tools):
        validation_context = ValidationContext(
            run_id=context.run_id,
            range_instance_id=context.range.range_instance_id,
            range_generation=context.range.range_generation,
            build_id=runtime.state.load_instance(context.range.range_instance_id).build_id,
        )
        oracle = StateOracleStore(runtime.state).load_for_context(validation_context)
        prop = oracle.properties[0]
        finding = CandidateFinding(
            finding_id=uuid4(),
            run_id=context.run_id,
            range_instance_id=context.range.range_instance_id,
            range_generation=context.range.range_generation,
            claim="partial finding",
            family=prop.family,
            asset_id=prop.object.object_id,
            security_property=prop.expectation,
            evidence=(EvidenceRef(evidence_id=uuid4(), action_id=uuid4(), description="proof"),),
        )
        submitted.append(finding)
        await events.append(
            FindingSubmitted(run_id=context.run_id, actor="solver", finding=finding)
        )
        raise TimeoutError("agent exhausted wall time")

    async def validate(self, finding, context):
        oracle = StateOracleStore(runtime.state).load_for_context(context)
        prop = oracle.properties[0]
        return ValidationResult(
            run_id=context.run_id,
            finding_id=finding.finding_id,
            status="validated",
            matched_property_id=prop.property_id,
            matched_root_cause_id=prop.root_cause_id,
        )

    monkeypatch.setattr(ScriptedSaasSolver, "run", agent_run)
    monkeypatch.setattr(DeterministicValidator, "validate", validate)
    outcome = await ScriptedExperimentRunner(runtime, events).run(scripted_spec())
    assert outcome.evaluation.status == "budget_exhausted"
    assert outcome.evaluation.score_valid is True
    assert outcome.evaluation.true_positives == 1
    assert outcome.evaluation.false_negatives == 4
    assert outcome.findings == tuple(submitted)
    assert len([item for item in events.items if isinstance(item, RunCompleted)]) == 1
