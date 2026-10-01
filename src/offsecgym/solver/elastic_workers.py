"""M6.3 deterministic dependency and budget-aware sequential worker scheduler."""

from __future__ import annotations

from uuid import UUID, uuid5

from offsecgym.interfaces import ToolRegistry
from offsecgym.schemas.domain import AgentContext, AgentResult, AgentTask
from offsecgym.schemas.scheduler import TaskBudgetRequest, TaskState
from offsecgym.schemas.specs import Budget
from offsecgym.solver.matched_workers import MatchedWorkerCoordinator, _PlannedWorker
from offsecgym.solver.workers import (
    WORKER_OBJECTIVES,
    WORKER_OUTPUT_CAP_TOKENS,
    FutureWorkerFloor,
)

# Frozen M6.3.0 heuristic. Bootstrap supplies IDs; refund waits for invoice work.
PRIORITY = ("identity", "invoice", "refund", "public", "document", "ticket")
DEPENDENCIES = {"refund": ("invoice",)}
REQUESTS = {
    "identity": TaskBudgetRequest(
        minimum_viable_tokens=20000,
        preferred_tokens=28000,
        max_tokens=40000,
        minimum_model_calls=1,
        max_model_calls=2,
        expected_actions=10,
        max_actions=10,
        expected_http_requests=10,
        max_http_requests=10,
    ),
    **{
        kind: TaskBudgetRequest(
            minimum_viable_tokens=29000,
            preferred_tokens=38000,
            max_tokens=55000,
            minimum_model_calls=2,
            max_model_calls=4,
            expected_actions=10,
            max_actions=10,
            expected_http_requests=10,
            max_http_requests=10,
        )
        for kind in ("document", "invoice", "ticket", "public")
    },
    "refund": TaskBudgetRequest(
        minimum_viable_tokens=40000,
        preferred_tokens=50000,
        max_tokens=65000,
        minimum_model_calls=3,
        max_model_calls=5,
        expected_actions=10,
        max_actions=10,
        expected_http_requests=10,
        max_http_requests=10,
    ),
}


class ElasticWorkerCoordinator(MatchedWorkerCoordinator):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, parallel=False, escrow=False, **kwargs)

    @staticmethod
    def _identifiers(run_id: UUID, kind: str) -> tuple[UUID, UUID]:
        return uuid5(run_id, f"m63-task:{kind}"), uuid5(run_id, f"m63-worker:{kind}")

    async def run(self, task: AgentTask, context: AgentContext, tools: ToolRegistry) -> AgentResult:
        if context.global_budget is None or context.range is None:
            raise ValueError("elastic workers require a visible range and global budget")
        budget = task.budget
        if any(
            getattr(budget, field) is None
            for field in ("max_total_tokens", "max_model_calls", "max_actions", "max_http_requests")
        ):
            raise ValueError("elastic scheduling requires explicit worker compute limits")
        objectives = {kind: (goal, entities) for kind, goal, entities in WORKER_OBJECTIVES}
        states: dict[str, TaskState] = {
            kind: "PENDING" if kind in DEPENDENCIES else "READY" for kind in PRIORITY
        }
        results: list[AgentResult] = []
        for kind in PRIORITY:
            dependencies = DEPENDENCIES.get(kind, ())
            if any(states[dependency] != "COMPLETED" for dependency in dependencies):
                states[kind] = "BLOCKED"
                continue
            states[kind] = "READY"
            request = REQUESTS[kind]
            task_id, worker_id = self._identifiers(context.run_id, kind)
            goal, entities = objectives[kind]
            cap = Budget(
                max_total_tokens=request.max_tokens,
                max_model_calls=request.max_model_calls,
                max_actions=request.max_actions,
                max_http_requests=request.max_http_requests,
                max_output_tokens_per_call=min(
                    budget.max_output_tokens_per_call or WORKER_OUTPUT_CAP_TOKENS,
                    WORKER_OUTPUT_CAP_TOKENS,
                ),
            )
            packet = await self.packet_builder.build(
                context, task_id, worker_id, goal, entities, cap, FutureWorkerFloor()
            )
            reason = await self.controller.schedule_elastic_task(
                context.run_id,
                worker_id,
                task_id,
                kind,
                request,
                states.copy(),
                budget,
                context.global_budget,
                objective=goal,
            )
            if reason:
                states[kind] = "BLOCKED"
                continue
            states[kind] = "RUNNING"
            planned = _PlannedWorker(kind, task_id, worker_id, goal, cap, packet)
            try:
                result = await self._run_one(planned, task, context, tools)
            except BaseException:
                states[kind] = "FAILED"
                await self.controller.cancel_unspawned_elastic_task(
                    context.run_id, worker_id, task_id
                )
                raise
            results.append(result)
            states[kind] = "FAILED" if result.status == "failed" else "COMPLETED"
        return AgentResult(
            task_id=task.task_id,
            status=(
                "failed"
                if any(item.status == "failed" for item in results)
                else "budget_exhausted"
                if any(item.status == "budget_exhausted" for item in results)
                else "completed"
            ),
            observation_ids=tuple(
                identifier for item in results for identifier in item.observation_ids
            ),
            candidate_finding_ids=tuple(
                identifier for item in results for identifier in item.candidate_finding_ids
            ),
        )
