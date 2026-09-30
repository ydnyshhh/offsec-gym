"""Run a scripted agent, independent validator, replay, and evaluator end to end."""

from __future__ import annotations

import asyncio
import hashlib
from uuid import UUID, uuid4

from offsecgym.evaluation import RunEvaluation, evaluate_run, infrastructure_failure
from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.interfaces import EventStore
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError
from offsecgym.runtime.manifests import BuildIntegrityError
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import (
    AgentContext,
    AgentResult,
    AgentTask,
    CandidateFinding,
    ExperimentContext,
    ValidationContext,
    ValidationResult,
    agent_visible_context,
)
from offsecgym.schemas.events import (
    FindingSubmitted,
    FindingValidated,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.scripted import ExperimentInfrastructureError, ScriptedSaasSolver
from offsecgym.validation import CloneReplayVerifier, DeterministicValidator


class EventFindingSink:
    def __init__(self, events: EventStore) -> None:
        self.events = events

    async def submit(self, finding: CandidateFinding) -> CandidateFinding:
        await self.events.append(
            FindingSubmitted(run_id=finding.run_id, actor="solver", finding=finding)
        )
        return finding

    async def read_run(self, run_id: UUID) -> tuple[CandidateFinding, ...]:
        return tuple(
            event.finding
            for event in await self.events.read_run(run_id)
            if isinstance(event, FindingSubmitted)
        )


class BoundGatewayTools:
    def __init__(self, gateway: ComposeActionGateway, context: ExperimentContext) -> None:
        self.gateway = gateway
        self.context = context

    async def execute(self, action: ActionRequest) -> ActionResult:
        if action.run_id != self.context.run_id:
            raise ValueError("tool action belongs to another run")
        return await self.gateway.execute(action, self.context)


class ScriptedExperimentOutcome:
    def __init__(
        self,
        run_id: UUID,
        build_id: UUID | None,
        agent_result: AgentResult | None,
        findings: tuple[CandidateFinding, ...],
        validations: tuple[ValidationResult, ...],
        evaluation: RunEvaluation,
        failure_reason: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.build_id = build_id
        self.agent_result = agent_result
        self.findings = findings
        self.validations = validations
        self.evaluation = evaluation
        self.failure_reason = failure_reason


class ScriptedExperimentRunner:
    def __init__(self, runtime: ComposeRangeRuntime, events: EventStore) -> None:
        self.runtime = runtime
        self.events = events

    async def run(self, spec: ExperimentSpec) -> ScriptedExperimentOutcome:
        if (
            spec.range.family != "saas"
            or spec.orchestrator != "scripted"
            or spec.validation != "deterministic"
        ):
            raise ValueError("Milestone 3 runner requires scripted deterministic SaaS spec")
        run_id = uuid4()
        instance_id: UUID | None = None
        build_id: UUID | None = None
        agent_result: AgentResult | None = None
        outcome: ScriptedExperimentOutcome | None = None
        findings_store = EventFindingSink(self.events)
        await self.events.append(
            RunStarted(
                run_id=run_id,
                actor="controller",
                experiment_hash=hashlib.sha256(spec.model_dump_json().encode()).hexdigest(),
            )
        )
        try:
            build_id = await self.runtime.build(spec.range)
            instance_id = await self.runtime.create_instance(build_id)
            status = await self.runtime.start_instance(instance_id)
            await self.events.append(
                RangeStarted(
                    run_id=run_id,
                    actor="controller",
                    build_id=build_id,
                    range_instance_id=instance_id,
                    range_generation=status.generation,
                )
            )
            metadata = await self.runtime.snapshot_metadata(instance_id)
            visible = agent_visible_context(metadata, visibility_policy="white_box_accounts")
            experiment = ExperimentContext(
                run_id=run_id,
                range_instance_id=instance_id,
                range_generation=status.generation,
                budget=spec.budget,
                allowed_identity_ids=visible.identity_ids,
            )
            gateway = ComposeActionGateway(self.runtime, self.events, min_interval_seconds=0)
            agent = ScriptedSaasSolver(findings_store)
            task = AgentTask(
                task_id=uuid4(),
                goal="Test SaaS authorization and data exposure boundaries",
                allowed_services=("saas",),
                budget=spec.budget,
            )
            agent_context = AgentContext(run_id=run_id, objective=task.goal, range=visible)
            tools = BoundGatewayTools(gateway, experiment)
            if spec.budget.max_wall_seconds is None:
                agent_result = await agent.run(task, agent_context, tools)
            else:
                agent_result = await asyncio.wait_for(
                    agent.run(task, agent_context, tools), spec.budget.max_wall_seconds
                )
            if agent_result.status != "completed":
                raise ExperimentInfrastructureError("scripted solver did not complete")
            findings = await findings_store.read_run(run_id)
            context = ValidationContext(
                run_id=run_id,
                range_instance_id=instance_id,
                range_generation=status.generation,
                build_id=build_id,
            )
            oracle_store = StateOracleStore(self.runtime.state)
            validator = DeterministicValidator(
                self.runtime.state,
                self.events,
                oracle_store,
                replay=CloneReplayVerifier(self.runtime, gateway),
            )
            validations: list[ValidationResult] = []
            for finding in findings:
                result = await validator.validate(finding, context)
                validations.append(result)
                await self.events.append(
                    FindingValidated(run_id=run_id, actor="validator", result=result)
                )
            evaluation = (
                infrastructure_failure(
                    len(findings),
                    inconclusive=sum(item.status == "inconclusive" for item in validations),
                )
                if any(item.status == "inconclusive" for item in validations)
                else evaluate_run(
                    findings, tuple(validations), oracle_store.load_for_context(context)
                )
            )
            outcome = ScriptedExperimentOutcome(
                run_id, build_id, agent_result, findings, tuple(validations), evaluation
            )
        except (
            ExperimentInfrastructureError,
            BuildIntegrityError,
            DockerCommandError,
            OSError,
            TimeoutError,
        ) as exc:
            findings = await findings_store.read_run(run_id)
            outcome = ScriptedExperimentOutcome(
                run_id,
                build_id,
                agent_result,
                findings,
                (),
                infrastructure_failure(len(findings)),
                failure_reason=type(exc).__name__,
            )
        finally:
            if instance_id is not None:
                try:
                    await self.runtime.destroy_instance(instance_id)
                except (DockerCommandError, OSError, TimeoutError, ValueError):
                    if outcome is not None:
                        outcome.evaluation = infrastructure_failure(len(outcome.findings))
                        outcome.failure_reason = "range_cleanup_failed"
                    else:
                        raise
        if outcome is None:
            raise RuntimeError("scripted experiment completed without an outcome")
        await self.events.append(
            RunCompleted(
                run_id=run_id,
                actor="controller",
                status="completed"
                if outcome.evaluation.status == "completed"
                else "environment_failed",
            )
        )
        return outcome
