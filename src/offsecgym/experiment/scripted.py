"""Run a scripted agent, independent validator, replay, and evaluator end to end."""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from offsecgym.evaluation import RunEvaluation, evaluate_run, unscored_run
from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.interfaces import EventStore
from offsecgym.providers.base import ProviderFailure, ProviderRequestError
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
    FindingProposal,
    ValidationContext,
    ValidationResult,
    agent_visible_context,
)
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.scripted import (
    AgentBudgetExhausted,
    ExperimentInfrastructureError,
    ScriptedSaasSolver,
)
from offsecgym.validation import CloneReplayVerifier, DeterministicValidator


class BoundFindingSink:
    def __init__(self, events: EventStore, context: ExperimentContext) -> None:
        self.events = events
        self.context = context

    async def submit(self, proposal: FindingProposal) -> CandidateFinding:
        if len({ref.evidence_id for ref in proposal.evidence}) != len(proposal.evidence):
            raise ValueError("finding proposal contains duplicate evidence")
        try:
            events = await self.events.read_run(self.context.run_id)
        except Exception as exc:
            raise ExperimentInfrastructureError("finding event stream unavailable") from exc
        for ref in proposal.evidence:
            requested = [
                event
                for event in events
                if isinstance(event, ActionRequested) and event.action_id == ref.action_id
            ]
            completed = [
                event
                for event in events
                if isinstance(event, ActionCompleted)
                and event.action_id == ref.action_id
                and event.evidence_id == ref.evidence_id
            ]
            if (
                len(requested) != 1
                or len(completed) != 1
                or requested[0].schema_version != "2"
                or requested[0].run_id != self.context.run_id
                or completed[0].run_id != self.context.run_id
                or requested[0].range_instance_id != self.context.range_instance_id
                or requested[0].range_generation != self.context.range_generation
                or requested[0].sequence_number <= 0
                or requested[0].sequence_number >= completed[0].sequence_number
            ):
                raise ValueError("finding evidence is outside the bound run or generation")
        finding = CandidateFinding(
            **proposal.model_dump(mode="python"),
            finding_id=uuid4(),
            run_id=self.context.run_id,
            range_instance_id=self.context.range_instance_id,
            range_generation=self.context.range_generation,
        )
        try:
            await self.events.append(
                FindingSubmitted(run_id=self.context.run_id, actor="solver", finding=finding)
            )
        except SQLAlchemyError as exc:
            raise ExperimentInfrastructureError("finding event storage unavailable") from exc
        return finding

    async def read_run(self, run_id: UUID) -> tuple[CandidateFinding, ...]:
        try:
            events = await self.events.read_run(run_id)
        except SQLAlchemyError as exc:
            raise ExperimentInfrastructureError("finding event storage unavailable") from exc
        return tuple(event.finding for event in events if isinstance(event, FindingSubmitted))


class BoundGatewayTools:
    def __init__(self, gateway: ComposeActionGateway, context: ExperimentContext) -> None:
        self.gateway = gateway
        self.context = context

    async def execute(self, action: ActionRequest) -> ActionResult:
        if action.run_id != self.context.run_id:
            raise ValueError("tool action belongs to another run")
        try:
            return await self.gateway.execute(action, self.context)
        except SQLAlchemyError as exc:
            raise ExperimentInfrastructureError("gateway event storage unavailable") from exc


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

    def _agent(self, findings_store: BoundFindingSink, spec: ExperimentSpec):
        return ScriptedSaasSolver(findings_store)

    def _supported(self, spec: ExperimentSpec) -> bool:
        return (
            spec.range.family == "saas"
            and spec.orchestrator == "scripted"
            and spec.validation == "deterministic"
        )

    async def run(self, spec: ExperimentSpec) -> ScriptedExperimentOutcome:
        if not self._supported(spec):
            raise ValueError("runner requires a supported deterministic SaaS experiment")
        run_id = uuid4()
        instance_id: UUID | None = None
        build_id: UUID | None = None
        agent_result: AgentResult | None = None
        findings: tuple[CandidateFinding, ...] = ()
        validations: list[ValidationResult] = []
        evaluation: RunEvaluation | None = None
        run_status = "completed"
        failure_reason: str | None = None
        phase = "setup"
        await self.events.append(
            RunStarted(
                run_id=run_id,
                actor="controller",
                experiment_hash=experiment_hash(spec),
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
            findings_store = BoundFindingSink(self.events, experiment)
            agent = self._agent(findings_store, spec)
            task = AgentTask(
                task_id=uuid4(),
                goal="Test SaaS authorization and data exposure boundaries",
                allowed_services=("saas",),
                budget=spec.budget,
            )
            agent_context = AgentContext(run_id=run_id, objective=task.goal, range=visible)
            tools = BoundGatewayTools(gateway, experiment)
            phase = "agent"
            try:
                if spec.budget.max_wall_seconds is None:
                    agent_result = await agent.run(task, agent_context, tools)
                else:
                    agent_result = await asyncio.wait_for(
                        agent.run(task, agent_context, tools), spec.budget.max_wall_seconds
                    )
                if agent_result.status == "budget_exhausted":
                    run_status = "budget_exhausted"
                elif agent_result.status == "cancelled":
                    run_status = "cancelled"
                elif agent_result.status != "completed":
                    run_status = "agent_failed"
                    failure_reason = agent_result.status
            except TimeoutError:
                run_status = "budget_exhausted"
                failure_reason = "wall_time_exhausted"
            except AgentBudgetExhausted as exc:
                run_status = "budget_exhausted"
                failure_reason = str(exc)
            except ProviderFailure as exc:
                run_status = "provider_failed"
                failure_reason = exc.reason_code
            except ProviderRequestError as exc:
                raise ExperimentInfrastructureError(exc.reason_code) from exc
            except (
                ExperimentInfrastructureError,
                BuildIntegrityError,
                DockerCommandError,
                OSError,
                SQLAlchemyError,
            ):
                raise
            except Exception as exc:
                run_status = "agent_failed"
                failure_reason = type(exc).__name__
            findings = await findings_store.read_run(run_id)
            if agent_result is None:
                agent_result = AgentResult(
                    task_id=task.task_id,
                    status="budget_exhausted" if run_status == "budget_exhausted" else "failed",
                    candidate_finding_ids=tuple(item.finding_id for item in findings),
                )
            phase = "validation"
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
                replay=CloneReplayVerifier(self.runtime, gateway, self.events),
            )
            for finding in findings:
                result = await validator.validate(finding, context)
                validations.append(result)
                await self.events.append(
                    FindingValidated(run_id=run_id, actor="validator", result=result)
                )
            if any(item.status == "inconclusive" for item in validations):
                run_status = "environment_failed"
                failure_reason = "validation_inconclusive"
            elif run_status not in {"provider_failed", "cancelled"}:
                evaluation = evaluate_run(
                    findings,
                    tuple(validations),
                    oracle_store.load_for_context(context),
                    status=run_status,
                )
        except asyncio.CancelledError:
            run_status = "cancelled"
            failure_reason = "controller_cancelled"
            raise
        except (
            ExperimentInfrastructureError,
            BuildIntegrityError,
            DockerCommandError,
            OSError,
            TimeoutError,
            SQLAlchemyError,
        ) as exc:
            run_status = "environment_failed"
            failure_reason = type(exc).__name__
        except Exception as exc:
            run_status = "validation_failed" if phase == "validation" else "environment_failed"
            failure_reason = type(exc).__name__
        finally:
            if instance_id is not None:
                try:
                    await self.runtime.destroy_instance(instance_id)
                except Exception:
                    run_status = "environment_failed"
                    failure_reason = "range_cleanup_failed"
            if run_status in {
                "environment_failed",
                "provider_failed",
                "validation_failed",
                "cancelled",
            }:
                evaluation = unscored_run(
                    run_status,
                    len(findings),
                    validated_count=sum(item.status == "validated" for item in validations),
                    inconclusive=sum(item.status == "inconclusive" for item in validations),
                )
            await self.events.append(
                RunCompleted(run_id=run_id, actor="controller", status=run_status)
            )
        if evaluation is None:
            raise RuntimeError("scripted experiment completed without an evaluation")
        outcome = ScriptedExperimentOutcome(
            run_id,
            build_id,
            agent_result,
            findings,
            tuple(validations),
            evaluation,
            failure_reason=failure_reason,
        )
        return outcome


def experiment_hash(spec: ExperimentSpec) -> str:
    """Hash semantic experiment input with stable object-key ordering."""
    canonical = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
