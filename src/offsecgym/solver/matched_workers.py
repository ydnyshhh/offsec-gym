"""M6.2.1 fixed-packet scheduling comparison over the frozen worker policy."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID, uuid4

from offsecgym.interfaces import ToolRegistry
from offsecgym.schemas.domain import AgentContext, AgentResult, AgentTask, CoverageClaim
from offsecgym.schemas.events import (
    CoverageUpdated,
    FindingSubmitted,
    WorkerBlocked,
    WorkerContractViolated,
    WorkerDebriefed,
    WorkerObjectiveAction,
    WorkerOriented,
    WorkerPacketPrepared,
    WorkerScheduled,
    WorkerStarted,
    WorldFactSubmitted,
)
from offsecgym.schemas.specs import Budget
from offsecgym.schemas.workers import WorkerDebrief, WorkerTaskPacket
from offsecgym.solver.monolithic import MonolithicSaasAgent
from offsecgym.solver.scripted import AgentBudgetExhausted, ExperimentInfrastructureError
from offsecgym.solver.workers import (
    MIN_MEANINGFUL_INPUT_TOKENS,
    WORKER_OBJECTIVES,
    WORKER_OUTPUT_CAP_TOKENS,
    FutureWorkerFloor,
    SequentialWorkerCoordinator,
    _WorkerTools,
)


@dataclass(frozen=True)
class _PlannedWorker:
    kind: str
    task_id: UUID
    worker_id: UUID
    objective: str
    budget: Budget
    packet: WorkerTaskPacket


def _portion(limit: int | None, index: int, count: int) -> int | None:
    return None if limit is None else limit // count + int(index < limit % count)


def _fixed_slice(budget: Budget, index: int, count: int) -> Budget:
    """Non-transferable slices sum to no more than the global compute limits."""
    cost = budget.max_cost_usd / count if budget.max_cost_usd is not None else None
    return Budget(
        max_total_tokens=_portion(budget.max_total_tokens, index, count),
        max_output_tokens_per_call=min(
            budget.max_output_tokens_per_call or WORKER_OUTPUT_CAP_TOKENS,
            WORKER_OUTPUT_CAP_TOKENS,
        ),
        max_model_calls=_portion(budget.max_model_calls, index, count),
        max_actions=_portion(budget.max_actions, index, count),
        max_http_requests=_portion(budget.max_http_requests, index, count),
        max_cost_usd=cost,
    )


class MatchedWorkerCoordinator(SequentialWorkerCoordinator):
    """Prepare identical policy packets, then vary only the execution semaphore."""

    def __init__(self, *args, parallel: bool, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.parallel = parallel

    async def _debrief_owned(
        self, run_id: UUID, task_id: UUID, worker_id: UUID, after: int
    ) -> WorkerDebrief:
        delta = [
            event for event in await self.events.read_run(run_id) if event.sequence_number > after
        ]
        facts = [
            event.fact
            for event in delta
            if isinstance(event, WorldFactSubmitted) and event.fact.source_worker_id == worker_id
        ]
        finding_ids = tuple(
            event.finding.finding_id
            for event in delta
            if isinstance(event, FindingSubmitted) and event.worker_id == worker_id
        )
        owned_claims = {
            claim.claim_id
            for claim in await self.world.coverage(run_id)
            if claim.task_id == task_id
        }
        completed_ids = tuple(
            event.claim_id
            for event in delta
            if isinstance(event, CoverageUpdated)
            and event.status == "completed"
            and event.claim_id in owned_claims
        )
        questions = tuple(
            f"{fact.predicate}: {str(fact.object_value)[:120]}"
            for fact in facts
            if fact.kind == "open_question"
        )[:12]
        return WorkerDebrief(
            task_id=task_id,
            worker_id=worker_id,
            new_fact_ids=tuple(fact.fact_id for fact in facts[:100]),
            candidate_finding_ids=finding_ids[:30],
            completed_coverage_ids=completed_ids[:30],
            open_questions=questions,
            recommended_followups=questions[:6],
        )

    async def _run_one(
        self,
        planned: _PlannedWorker,
        task: AgentTask,
        context: AgentContext,
        tools: ToolRegistry,
    ) -> AgentResult:
        assert context.global_budget is not None
        task_id, worker_id = planned.task_id, planned.worker_id
        claim = CoverageClaim(
            claim_id=uuid4(),
            run_id=context.run_id,
            task_id=task_id,
            component=planned.kind,
            objective=planned.objective,
        )
        async with self.controller.worker_guard(context.run_id, worker_id):
            spawned = claimed = False
            try:
                reason = await self.controller.spawn_worker(
                    context.run_id,
                    worker_id,
                    task_id,
                    planned.objective,
                    context.global_budget,
                )
                if reason:
                    raise AgentBudgetExhausted(reason)
                spawned = True
                await self.world.claim_coverage(claim)
                claimed = True
                packet_event = await self.events.append(
                    WorkerPacketPrepared(
                        run_id=context.run_id,
                        actor="coordinator",
                        worker_id=worker_id,
                        task_id=task_id,
                        packet=planned.packet,
                    )
                )
                reason = await self.controller.start_worker(
                    context.run_id, worker_id, task_id, context.global_budget
                )
                if reason:
                    raise ExperimentInfrastructureError(reason)
            except BaseException:
                if claimed:
                    await self.world.update_coverage(
                        context.run_id, claim.claim_id, task_id, "released"
                    )
                if spawned:
                    await self.controller.abort_spawned_worker(
                        context.run_id, worker_id, task_id, "worker_setup_failed"
                    )
                raise
            starts = [
                event
                for event in await self.events.read_run(context.run_id)
                if isinstance(event, WorkerStarted) and event.worker_id == worker_id
            ]
            start_sequence = starts[-1].sequence_number
            worker_task = AgentTask(
                task_id=task_id,
                worker_id=worker_id,
                goal=planned.objective,
                allowed_services=task.allowed_services,
                budget=planned.budget,
            )
            worker_context = context.model_copy(
                update={
                    "objective": planned.objective,
                    "worker_packet": planned.packet,
                    "worker_packet_sequence": packet_event.sequence_number,
                }
            )
            agent = MonolithicSaasAgent(
                self.provider,
                self.model,
                self.findings,
                self.events,
                self.state_root,
                memory="structured",
            )
            status = "completed"
            result = AgentResult(task_id=task_id, status="completed")
            heartbeat: asyncio.Task | None = None
            try:
                owner = asyncio.current_task()
                assert owner is not None
                heartbeat = asyncio.create_task(
                    self._heartbeat_worker(context.run_id, worker_id, task_id, owner)
                )
                result = await agent.run(
                    worker_task,
                    worker_context,
                    _WorkerTools(
                        tools,
                        task_id,
                        worker_id,
                        planned.budget,
                        self.controller,
                        context.run_id,
                        context.global_budget,
                        FutureWorkerFloor(),
                    ),
                )
                status = result.status
            except AgentBudgetExhausted:
                status = "budget_exhausted"
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            except Exception:
                status = "failed"
                raise
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat
                worker_delta = [
                    event
                    for event in await self.events.read_run(context.run_id)
                    if event.sequence_number > start_sequence
                    and getattr(event, "worker_id", None) == worker_id
                ]
                outcomes = [
                    event
                    for event in worker_delta
                    if isinstance(event, (WorkerObjectiveAction, WorkerBlocked))
                ]
                if not outcomes and status != "cancelled":
                    await self.events.append(
                        WorkerContractViolated(
                            run_id=context.run_id,
                            actor="controller",
                            worker_id=worker_id,
                            task_id=task_id,
                            reason_code=(
                                "task_block_required"
                                if any(isinstance(event, WorkerOriented) for event in worker_delta)
                                else f"objective_unattempted_{status}"
                            ),
                        )
                    )
                    status = "failed"
                await self.world.update_coverage(
                    context.run_id,
                    claim.claim_id,
                    task_id,
                    "completed"
                    if any(isinstance(event, WorkerObjectiveAction) for event in outcomes)
                    else "released",
                )
                for other in await self.world.coverage(context.run_id):
                    if other.task_id == task_id and other.status == "active":
                        await self.world.update_coverage(
                            context.run_id, other.claim_id, task_id, "released"
                        )
                debrief = await self._debrief_owned(
                    context.run_id, task_id, worker_id, start_sequence
                )
                await self.events.append(
                    WorkerDebriefed(
                        run_id=context.run_id,
                        actor="coordinator",
                        worker_id=worker_id,
                        task_id=task_id,
                        new_fact_ids=debrief.new_fact_ids,
                        candidate_finding_ids=debrief.candidate_finding_ids,
                        coverage_claim_ids=debrief.completed_coverage_ids,
                        open_questions=debrief.open_questions,
                        recommended_followups=debrief.recommended_followups,
                    )
                )
                await self.controller.finish_worker(context.run_id, worker_id, task_id, status)
            return result.model_copy(update={"status": status})

    async def run(self, task: AgentTask, context: AgentContext, tools: ToolRegistry) -> AgentResult:
        budget = task.budget
        if context.global_budget is None or context.range is None or budget.max_model_calls is None:
            raise ValueError("matched workers require a visible range and global model budget")
        count = len(WORKER_OBJECTIVES)
        output = min(
            budget.max_output_tokens_per_call or WORKER_OUTPUT_CAP_TOKENS, WORKER_OUTPUT_CAP_TOKENS
        )
        if budget.max_total_tokens is not None and budget.max_total_tokens < count * (
            MIN_MEANINGFUL_INPUT_TOKENS + output
        ):
            raise ValueError("global token budget cannot fund one reserved turn per worker")
        planned = []
        for index, (kind, objective, relevant_types) in enumerate(WORKER_OBJECTIVES):
            task_id, worker_id = uuid4(), uuid4()
            slice_budget = _fixed_slice(budget, index, count)
            packet = await self.packet_builder.build(
                context,
                task_id,
                worker_id,
                objective,
                relevant_types,
                slice_budget,
                FutureWorkerFloor(),
            )
            planned.append(
                _PlannedWorker(kind, task_id, worker_id, objective, slice_budget, packet)
            )
        semaphore = asyncio.Semaphore(count if self.parallel else 1)
        for item in planned:
            await self.events.append(
                WorkerScheduled(
                    run_id=context.run_id,
                    actor="coordinator",
                    worker_id=item.worker_id,
                    task_id=item.task_id,
                    objective=item.objective,
                    scheduling=("matched_parallel" if self.parallel else "matched_sequential"),
                )
            )

        async def launch(item: _PlannedWorker) -> AgentResult:
            async with semaphore:
                return await self._run_one(item, task, context, tools)

        tasks = [asyncio.create_task(launch(item)) for item in planned]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for running in tasks:
                running.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return AgentResult(
            task_id=task.task_id,
            status=(
                "failed"
                if any(item.status == "failed" for item in results)
                else "budget_exhausted"
                if any(item.status == "budget_exhausted" for item in results)
                else "completed"
            ),
            observation_ids=tuple(fact_id for item in results for fact_id in item.observation_ids),
            candidate_finding_ids=tuple(
                finding_id for item in results for finding_id in item.candidate_finding_ids
            ),
        )
