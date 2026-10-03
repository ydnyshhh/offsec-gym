"""Prospective read-only reporting after a frozen worker probe and before validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import uuid4

from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.experiment.workers import WorkerExperimentRunner
from offsecgym.providers.base import ProviderFailure, ProviderRequestError
from offsecgym.research.m65_reporter import ReadOnlyReporter
from offsecgym.research.m65_witness_packet import ReadOnlyReporterTools, build_reporter_bundle
from offsecgym.schemas.domain import CandidateFinding, ExperimentContext
from offsecgym.schemas.events import ReporterFinished, ReporterStarted
from offsecgym.schemas.specs import Budget, ExperimentSpec
from offsecgym.solver.scripted import ExperimentInfrastructureError


def combined_budget(probe: Budget, reporter: Budget) -> Budget:
    """Reserve an explicit extra model allowance while keeping probe task caps intact."""
    if (
        probe.max_total_tokens is None
        or probe.max_model_calls is None
        or reporter.max_total_tokens is None
        or reporter.max_model_calls is None
        or reporter.max_output_tokens_per_call is None
        or reporter.max_wall_seconds is None
    ):
        raise ValueError("both stages require explicit token/call and reporter turn/wall limits")
    if (probe.max_cost_usd is None) != (reporter.max_cost_usd is None):
        raise ValueError("probe and reporter cost caps must both be set or both absent")
    return Budget.model_validate(
        {
            **probe.model_dump(),
            "max_total_tokens": probe.max_total_tokens + reporter.max_total_tokens,
            "max_model_calls": probe.max_model_calls + reporter.max_model_calls,
            "max_output_tokens_per_call": max(
                probe.max_output_tokens_per_call or 16,
                reporter.max_output_tokens_per_call,
            ),
            "max_cost_usd": (
                probe.max_cost_usd + reporter.max_cost_usd
                if probe.max_cost_usd is not None and reporter.max_cost_usd is not None
                else None
            ),
        }
    )


class ReporterRecoveryRunner(WorkerExperimentRunner):
    """Keep the probe task budget and policy; add one isolated reporting stage."""

    def __init__(self, runtime, events, provider, reporter_budget: Budget) -> None:
        super().__init__(runtime, events, provider)
        self.reporter_budget = reporter_budget

    def _supported(self, spec: ExperimentSpec) -> bool:
        return (
            super()._supported(spec)
            and spec.orchestrator == "admitted_sequential_workers"
            and spec.model is not None
        )

    def _controller_budget(self, spec: ExperimentSpec) -> Budget:
        return combined_budget(super()._controller_budget(spec), self.reporter_budget)

    def _experiment_hash(self, spec: ExperimentSpec) -> str:
        record = {
            "probe_spec": spec.model_dump(mode="json"),
            "reporter_budget": self.reporter_budget.model_dump(mode="json"),
            "reporter_contract_version": "m65-read-only-v1",
        }
        raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    async def _after_agent(
        self,
        spec: ExperimentSpec,
        context: ExperimentContext,
        findings_store: BoundFindingSink,
        original_findings: tuple[CandidateFinding, ...],
        run_status: str,
    ) -> None:
        if spec.model is None:
            raise ValueError("reporter requires a model")
        trace = await self.events.read_run(context.run_id)
        bundle = build_reporter_bundle(
            trace, self.runtime.state.root, expected_run_id=context.run_id
        )
        packet_sha = hashlib.sha256(bundle.packet.model_dump_json().encode()).hexdigest()
        reporter_id, task_id = uuid4(), uuid4()
        await self.events.append(
            ReporterStarted(
                run_id=context.run_id,
                actor="controller",
                reporter_id=reporter_id,
                task_id=task_id,
                packet_sha256=packet_sha,
                budget=self.reporter_budget,
                original_finding_ids=tuple(item.finding_id for item in original_findings),
            )
        )
        status = "failed"
        reason: str | None = None
        submitted = ()
        lookups = ()
        pending_error: Exception | None = None
        try:
            reporter = ReadOnlyReporter(
                self.provider, spec.model, self.events, self.runtime.state.root
            )
            result = await asyncio.wait_for(
                reporter.run(
                    bundle,
                    ReadOnlyReporterTools(bundle, findings_store),
                    reporter_budget=self.reporter_budget,
                    global_budget=context.budget,
                ),
                timeout=self.reporter_budget.max_wall_seconds,
            )
            status = result.status
            reason = result.reason_code
            submitted = result.submitted_finding_ids
            lookups = result.evidence_lookup_action_ids
        except TimeoutError:
            status = "budget_exhausted"
            reason = "reporter_wall_time_exhausted"
        except ProviderFailure as exc:
            status = "provider_failed"
            reason = exc.reason_code
        except ProviderRequestError as exc:
            reason = exc.reason_code
            pending_error = ExperimentInfrastructureError(exc.reason_code)
        except Exception as exc:
            reason = type(exc).__name__
            pending_error = exc
        finally:
            post = await findings_store.read_run(context.run_id)
            observed = tuple(item.finding_id for item in post[len(original_findings) :])
            if submitted and submitted != observed:
                raise ExperimentInfrastructureError("reporter_submission_event_mismatch")
            await self.events.append(
                ReporterFinished(
                    run_id=context.run_id,
                    actor="controller",
                    reporter_id=reporter_id,
                    task_id=task_id,
                    status=status,
                    submitted_finding_ids=observed,
                    evidence_lookup_action_ids=lookups,
                    reason_code=reason,
                )
            )
        if pending_error is not None:
            raise pending_error
