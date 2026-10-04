"""M6.5.2 paired reporting over one immutable monolithic probe prefix."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from offsecgym.evaluation import RunEvaluation, evaluate_run, unscored_run
from offsecgym.experiment.bootstrapped_monolithic import BootstrappedMonolithicExperimentRunner
from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.providers.artifacts import payload_sha256
from offsecgym.providers.base import ProviderFailure, ProviderRequestError
from offsecgym.research.m65_reporter import ReadOnlyReporter
from offsecgym.research.m65_witness_packet import ReadOnlyReporterTools, build_reporter_bundle
from offsecgym.research.m652_checkpoint import ProbeCheckpoint, ProbeCheckpointStore
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import CandidateFinding, ExperimentContext, ValidationContext
from offsecgym.schemas.events import (
    FindingValidated,
    ReporterFinished,
    ReporterStarted,
    ReportingBranchStarted,
    ReportingPrefixRejected,
    RunCompleted,
)
from offsecgym.schemas.specs import ExperimentSpec, ReporterBudget
from offsecgym.solver.monolithic import MonolithicSaasAgent
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.reporting_branch import ReportingBranchStore
from offsecgym.validation import CloneReplayVerifier, DeterministicValidator


@dataclass(frozen=True)
class ReportingArmOutcome:
    branch_id: UUID
    arm: str
    evaluation: RunEvaluation
    reporter_reason: str | None


def paired_experiment_hash(
    spec: ExperimentSpec, reporter_budget: ReporterBudget, checkpoint_after_calls: int
) -> str:
    record = {
        "probe": spec.model_dump(mode="json"),
        "reporter_budget": reporter_budget.model_dump(mode="json"),
        "checkpoint_after_calls": checkpoint_after_calls,
        "version": "m652-paired-context-v1",
    }
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def arm_order_for_spec(spec: ExperimentSpec) -> tuple[str, str]:
    order = ("fresh", "continuation")
    if (
        hashlib.sha256(f"{spec.seed}:{spec.range.seed}:{spec.range.patched}".encode()).digest()[0]
        & 1
    ):
        return tuple(reversed(order))
    return order


class PairedReportingContextRunner(BootstrappedMonolithicExperimentRunner):
    """Opt-in source probe followed by fresh and authentic-carry reporting arms.

    This runner records study data but does not select seeds or make a costed
    collection decision. It requires PostgreSQL branch storage and a separate
    frozen manifest before any live use.
    """

    def __init__(
        self,
        runtime,
        events: PostgresEventStore,
        provider,
        reporter_budget: ReporterBudget,
        *,
        checkpoint_after_calls: int,
    ) -> None:
        super().__init__(runtime, events, provider)
        if checkpoint_after_calls < 1:
            raise ValueError("checkpoint_after_calls must be positive")
        if any(
            value is None
            for value in (
                reporter_budget.max_total_tokens,
                reporter_budget.max_model_calls,
                reporter_budget.max_output_tokens_per_call,
                reporter_budget.max_wall_seconds,
            )
        ):
            raise ValueError("paired reporter requires explicit token, call, output, and wall caps")
        self.reporter_budget = reporter_budget
        self.checkpoint_after_calls = checkpoint_after_calls
        self.checkpoints = ProbeCheckpointStore(events, runtime.state.root)
        self.arm_outcomes: dict[UUID, tuple[ReportingArmOutcome, ...]] = {}

    def _experiment_hash(self, spec: ExperimentSpec) -> str:
        return paired_experiment_hash(spec, self.reporter_budget, self.checkpoint_after_calls)

    def _agent(self, findings_store: BoundFindingSink, spec: ExperimentSpec):
        if spec.model is None or spec.budget.max_model_calls is None:
            raise ValueError("paired reporting requires an explicit probe model and call cap")
        if self.checkpoint_after_calls > spec.budget.max_model_calls:
            raise ValueError("checkpoint call limit exceeds probe call budget")
        return MonolithicSaasAgent(
            self.provider,
            spec.model,
            findings_store,
            self.events,
            self.runtime.state.root,
            memory="structured",
            bootstrap_context=True,
            checkpoint_store=self.checkpoints,
            checkpoint_after_calls=self.checkpoint_after_calls,
        )

    async def _after_agent(
        self,
        spec: ExperimentSpec,
        context: ExperimentContext,
        findings_store: BoundFindingSink,
        original_findings: tuple[CandidateFinding, ...],
        run_status: str,
    ) -> None:
        if spec.model is None:
            raise ValueError("paired reporter requires a model")
        if run_status not in {"completed", "budget_exhausted"}:
            await self.events.append(
                ReportingPrefixRejected(
                    run_id=context.run_id,
                    actor="controller",
                    reason_code="probe_status_not_eligible",
                )
            )
            return
        try:
            checkpoint = await self.checkpoints.load_latest(context.run_id)
            trace = list(await self.events.read_run(context.run_id))
            if trace[-1].type != "probe_checkpoint_saved":
                raise ValueError("probe continued after its selected checkpoint")
            bundle = build_reporter_bundle(
                trace, self.runtime.state.root, expected_run_id=context.run_id
            )
        except (OSError, ValueError) as exc:
            await self.events.append(
                ReportingPrefixRejected(
                    run_id=context.run_id,
                    actor="controller",
                    reason_code=f"invalid_source_prefix_{type(exc).__name__}",
                )
            )
            return

        carry = self._continuation_items(checkpoint)
        source_sha = bundle.packet.source_trace_sha256
        branches = {
            arm: await ReportingBranchStore.create(
                self.events,
                context.run_id,
                arm=arm,
                checkpoint_id=checkpoint.checkpoint_id,
            )
            for arm in ("fresh", "continuation")
        }
        order = arm_order_for_spec(spec)
        results: dict[str, ReportingArmOutcome] = {}
        for arm in order:
            branch = branches[arm]
            context_items = carry if arm == "continuation" else ()
            result = await self._run_arm(
                spec,
                context,
                branch,
                bundle,
                source_sha,
                original_findings,
                context_items,
                arm,
            )
            results[arm] = result
        self.arm_outcomes[context.run_id] = (results["fresh"], results["continuation"])

    @staticmethod
    def _continuation_items(checkpoint: ProbeCheckpoint) -> tuple[dict[str, object], ...]:
        items: list[dict[str, object]] = [*checkpoint.base_items, *checkpoint.carry_items]
        if checkpoint.working_state_text:
            items.append({"role": "user", "content": checkpoint.working_state_text})
        return tuple(items)

    async def _run_arm(
        self,
        spec: ExperimentSpec,
        context: ExperimentContext,
        branch: ReportingBranchStore,
        bundle,
        source_sha: str,
        original_findings: tuple[CandidateFinding, ...],
        context_items: tuple[dict[str, object], ...],
        arm: str,
    ) -> ReportingArmOutcome:
        assert spec.model is not None
        await branch.append(
            ReportingBranchStarted(
                run_id=context.run_id,
                actor="controller",
                branch_id=branch.branch_id,
                arm=arm,
                checkpoint_id=(await self.checkpoints.load_latest(context.run_id)).checkpoint_id,
                source_trace_sha256=source_sha,
                initial_context_sha256=payload_sha256({"items": list(context_items)}),
            )
        )
        reporter_id, task_id = uuid4(), uuid4()
        await branch.append(
            ReporterStarted(
                run_id=context.run_id,
                actor="controller",
                reporter_id=reporter_id,
                task_id=task_id,
                packet_sha256=bundle.packet.bundle_sha256,
                source_trace_sha256=source_sha,
                source_build_id=bundle.packet.source_build_id,
                budget=self.reporter_budget,
                original_finding_ids=tuple(item.finding_id for item in original_findings),
            )
        )
        sink = BoundFindingSink(branch, context)
        reporter = ReadOnlyReporter(
            self.provider,
            spec.model,
            branch,
            self.runtime.state.root,
            branch_id=branch.branch_id,
        )
        status = "failed"
        failed_run_status = "agent_failed"
        reason: str | None = None
        lookups: tuple[UUID, ...] = ()
        expected_submitted: tuple[UUID, ...] = ()
        reporter_returned = False
        try:
            result = await asyncio.wait_for(
                reporter.run(
                    bundle,
                    ReadOnlyReporterTools(bundle, sink),
                    reporter_budget=self.reporter_budget,
                    global_budget=context.budget,
                    reporter_id=reporter_id,
                    task_id=task_id,
                    initial_context_items=context_items,
                ),
                timeout=self.reporter_budget.max_wall_seconds,
            )
            status = result.status
            reason = result.reason_code
            lookups = result.evidence_lookup_action_ids
            expected_submitted = result.submitted_finding_ids
            reporter_returned = True
        except TimeoutError:
            status, reason = "budget_exhausted", "reporter_wall_time_exhausted"
        except ProviderFailure as exc:
            status, reason = "provider_failed", exc.reason_code
        except ProviderRequestError as exc:
            status, reason = "failed", exc.reason_code
            failed_run_status = "environment_failed"
        except Exception as exc:
            status, reason = "failed", type(exc).__name__
            failed_run_status = "environment_failed"
        finally:
            findings = await sink.read_run(context.run_id)
            new_ids = tuple(item.finding_id for item in findings[len(original_findings) :])
            if reporter_returned and expected_submitted != new_ids:
                status = "failed"
                reason = "reporter_submission_event_mismatch"
                failed_run_status = "environment_failed"
            await branch.append(
                ReporterFinished(
                    run_id=context.run_id,
                    actor="controller",
                    reporter_id=reporter_id,
                    task_id=task_id,
                    status=status,
                    submitted_finding_ids=new_ids,
                    evidence_lookup_action_ids=lookups,
                    reason_code=reason,
                )
            )

        validation = ValidationContext(
            run_id=context.run_id,
            range_instance_id=context.range_instance_id,
            range_generation=context.range_generation,
            build_id=bundle.packet.source_build_id,
        )
        oracle = StateOracleStore(self.runtime.state)
        gateway = ComposeActionGateway(self.runtime, self.events, min_interval_seconds=0)
        validator = DeterministicValidator(
            self.runtime.state,
            branch,
            oracle,
            replay=CloneReplayVerifier(self.runtime, gateway, self.events),
        )
        validations = []
        try:
            for finding in findings:
                verdict = await validator.validate(finding, validation)
                validations.append(verdict)
                await branch.append(
                    FindingValidated(run_id=context.run_id, actor="validator", result=verdict)
                )
        except Exception as exc:
            evaluation = unscored_run(
                "validation_failed",
                len(findings),
                validated_count=sum(item.status == "validated" for item in validations),
                inconclusive=sum(item.status == "inconclusive" for item in validations),
            )
            await branch.append(
                RunCompleted(run_id=context.run_id, actor="controller", status="validation_failed")
            )
            return ReportingArmOutcome(
                branch.branch_id, arm, evaluation, f"validation_{type(exc).__name__}"
            )
        run_status = (
            "provider_failed"
            if status == "provider_failed"
            else failed_run_status
            if status == "failed"
            else "environment_failed"
            if any(item.status == "inconclusive" for item in validations)
            else status
        )
        evaluation = (
            evaluate_run(
                findings,
                tuple(validations),
                oracle.load_for_context(validation),
                status=run_status,
            )
            if run_status in {"completed", "budget_exhausted", "agent_failed"}
            else unscored_run(
                run_status,
                len(findings),
                validated_count=sum(item.status == "validated" for item in validations),
                inconclusive=sum(item.status == "inconclusive" for item in validations),
            )
        )
        await branch.append(
            RunCompleted(run_id=context.run_id, actor="controller", status=run_status)
        )
        return ReportingArmOutcome(branch.branch_id, arm, evaluation, reason)
