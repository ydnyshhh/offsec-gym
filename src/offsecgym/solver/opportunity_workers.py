"""M6.3.1 sequential admission policy with evidence-based readiness."""

from __future__ import annotations

from itertools import combinations
from uuid import UUID, uuid5

from offsecgym.interfaces import ToolRegistry
from offsecgym.schemas.domain import AgentContext, AgentResult, AgentTask, EntityRef
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    PrerequisiteBootstrapCompleted,
    TaskStateEvaluated,
    WorkerObjectiveAction,
    WorldFactSubmitted,
)
from offsecgym.schemas.scheduler import AdmissionTask, TaskState
from offsecgym.schemas.specs import Budget
from offsecgym.solver.elastic_workers import REQUESTS, ElasticWorkerCoordinator
from offsecgym.solver.matched_workers import _PlannedWorker
from offsecgym.solver.workers import WORKER_OBJECTIVES, WORKER_OUTPUT_CAP_TOKENS, FutureWorkerFloor

# Integer utilities are deliberately fixed and inspectable. Invoice unlocks
# refund; public tests a distinct exposure surface; identity is only useful
# when its trusted state predicate is not yet satisfied.
UTILITY = {
    "identity": 100,
    "invoice": 220,
    "refund": 240,
    "public": 140,
    "document": 90,
    "ticket": 80,
}


def _identity_state(trace, identity_ids: tuple[UUID, ...], *, bootstrap_only: bool):
    completed = {
        event.action_id
        for event in trace
        if isinstance(event, ActionCompleted) and event.http_status == 200
    }
    if bootstrap_only:
        bootstrap = {
            event.action_id
            for event in trace
            if isinstance(event, ActionRequested) and event.source_phase == "bootstrap"
        }
        completed &= bootstrap
        marker = next(
            (event for event in trace if isinstance(event, PrerequisiteBootstrapCompleted)), None
        )
        if marker is None or marker.identity_count < len(identity_ids):
            return False, (), "bootstrap_identity_incomplete"
    facts = [
        event
        for event in trace
        if isinstance(event, WorldFactSubmitted)
        and event.actor == "controller"
        and event.fact.subject.entity_type == "identity"
        and event.fact.status in {"observed", "corroborated", "validated"}
        and event.fact.source_action_ids
        and set(event.fact.source_action_ids) <= completed
    ]
    source_ids = []
    for identity_id in identity_ids:
        role = next(
            (
                event
                for event in facts
                if event.fact.subject.entity_id == identity_id
                and event.fact.predicate == "role"
                and isinstance(event.fact.object_value, str)
            ),
            None,
        )
        if role is None:
            return False, tuple(source_ids), "identity_role_missing"
        source_ids.append(role.event_id)
        if role.fact.object_value in {"member", "workspace_admin"}:
            membership = next(
                (
                    event
                    for event in facts
                    if event.fact.subject.entity_id == identity_id
                    and event.fact.predicate == "member_of"
                    and isinstance(event.fact.object_value, EntityRef)
                ),
                None,
            )
            if membership is None:
                return False, tuple(source_ids), "identity_membership_missing"
            source_ids.append(membership.event_id)
    return True, tuple(source_ids), "identity_map_observed"


def _invoice_state(trace):
    marker = next(
        (event for event in trace if isinstance(event, PrerequisiteBootstrapCompleted)), None
    )
    if marker is None or marker.invoice_count < 1:
        return False, (), "invoice_targets_unknown"
    bootstrap_actions = {
        event.action_id
        for event in trace
        if isinstance(event, ActionRequested) and event.source_phase == "bootstrap"
    }
    targets = {
        event.fact.subject.entity_id
        for event in trace
        if isinstance(event, WorldFactSubmitted)
        and event.actor == "controller"
        and event.fact.subject.entity_type == "invoice"
        and event.fact.status == "observed"
        and set(event.fact.source_action_ids) & bootstrap_actions
    }
    if not targets:
        return False, (marker.event_id,), "invoice_targets_unverified"
    completed = {
        event.action_id: event
        for event in trace
        if isinstance(event, ActionCompleted)
        and event.http_status == 200
        and event.evidence_id is not None
    }
    objective = [
        event
        for event in trace
        if isinstance(event, WorkerObjectiveAction)
        and event.route_family == "GET /api/invoices/{id}"
        and event.action_id in completed
    ]
    for action in objective:
        state = next(
            (
                event
                for event in trace
                if isinstance(event, WorldFactSubmitted)
                and event.actor == "controller"
                and event.fact.subject.entity_type == "invoice"
                and event.fact.subject.entity_id in targets
                and event.fact.predicate == "status"
                and event.fact.status in {"observed", "corroborated", "validated"}
                and action.action_id in event.fact.source_action_ids
            ),
            None,
        )
        if state is not None:
            return (
                True,
                (
                    marker.event_id,
                    action.event_id,
                    completed[action.action_id].event_id,
                    state.event_id,
                ),
                "invoice_state_observed",
            )
    return False, (marker.event_id,), "invoice_detail_state_missing"


def _objective_reached(trace, worker_id: UUID) -> bool:
    completed = {event.action_id for event in trace if isinstance(event, ActionCompleted)}
    return any(
        isinstance(event, WorkerObjectiveAction)
        and event.worker_id == worker_id
        and event.action_id in completed
        for event in trace
    )


def choose_admission(
    ready: tuple[str, ...], capacity: dict[str, int], *, forecast_refund: bool
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Enumerate six-task subsets and maximize fixed utility under all minima."""
    best: tuple[int, int, tuple[str, ...], tuple[str, ...]] | None = None
    for size in range(len(ready) + 1):
        for subset in combinations(sorted(ready), size):
            forecast = ("refund",) if forecast_refund and "invoice" in subset else ()
            portfolio = (*subset, *forecast)
            demand = {
                "tokens": sum(REQUESTS[kind].minimum_viable_tokens for kind in portfolio),
                "calls": sum(REQUESTS[kind].minimum_model_calls for kind in portfolio),
                "actions": sum(REQUESTS[kind].expected_actions for kind in portfolio),
                "http": sum(REQUESTS[kind].expected_http_requests for kind in portfolio),
            }
            if any(demand[key] > capacity[key] for key in demand):
                continue
            candidate = (
                sum(UTILITY[kind] for kind in subset),
                len(subset),
                tuple(reversed(subset)),
                forecast,
            )
            if best is None or candidate > best:
                best = candidate
    if best is None:
        return (), ()
    return tuple(reversed(best[2])), best[3]


class OpportunityWorkerCoordinator(ElasticWorkerCoordinator):
    @staticmethod
    def _identifiers(run_id: UUID, kind: str) -> tuple[UUID, UUID]:
        return uuid5(run_id, f"m631-task:{kind}"), uuid5(run_id, f"m631-worker:{kind}")

    async def run(self, task: AgentTask, context: AgentContext, tools: ToolRegistry) -> AgentResult:
        if context.global_budget is None or context.range is None:
            raise ValueError("admission workers require a visible range and global budget")
        budget = task.budget
        objectives = {kind: (goal, entities) for kind, goal, entities in WORKER_OBJECTIVES}
        attempted: set[str] = set()
        results: list[AgentResult] = []
        previous_evaluations: dict[str, bool] = {}
        last_states: dict[str, TaskState] = {}
        try:
            for _ in range(len(REQUESTS) + 1):
                trace = await self.events.read_run(context.run_id)
                identities = tuple(context.range.identity_ids)
                bootstrap_identity, bootstrap_sources, _ = _identity_state(
                    trace, identities, bootstrap_only=True
                )
                identity_ready, identity_sources, identity_reason = _identity_state(
                    trace, identities, bootstrap_only=False
                )
                invoice_ready, invoice_sources, invoice_reason = _invoice_state(trace)
                for kind, satisfied, sources, reason, predicate in (
                    (
                        "identity",
                        identity_ready,
                        bootstrap_sources if bootstrap_identity else identity_sources,
                        "bootstrap_identity_satisfied" if bootstrap_identity else identity_reason,
                        "observed_identity_map",
                    ),
                    (
                        "refund",
                        invoice_ready,
                        invoice_sources,
                        invoice_reason,
                        "invoice_targets_and_observed_detail_state",
                    ),
                ):
                    if previous_evaluations.get(kind) != satisfied:
                        await self.events.append(
                            TaskStateEvaluated(
                                run_id=context.run_id,
                                actor="scheduler",
                                kind=kind,
                                predicate=predicate,
                                satisfied=satisfied,
                                source_event_ids=sources,
                                reason_code=reason,
                            )
                        )
                        previous_evaluations[kind] = satisfied
                states: dict[str, TaskState] = {}
                for kind in REQUESTS:
                    if kind == "identity":
                        states[kind] = (
                            "COMPLETED"
                            if identity_ready
                            else "FAILED"
                            if kind in attempted
                            else "READY"
                        )
                    elif kind == "refund":
                        worker_id = self._identifiers(context.run_id, kind)[1]
                        states[kind] = (
                            "COMPLETED"
                            if kind in attempted and _objective_reached(trace, worker_id)
                            else "FAILED"
                            if kind in attempted
                            else "READY"
                            if invoice_ready
                            else "BLOCKED"
                            if "invoice" in attempted
                            else "PENDING"
                        )
                    else:
                        worker_id = self._identifiers(context.run_id, kind)[1]
                        states[kind] = (
                            "COMPLETED"
                            if kind in attempted and _objective_reached(trace, worker_id)
                            else "FAILED"
                            if kind in attempted
                            else "READY"
                            if identity_ready
                            else "PENDING"
                        )
                last_states = states
                capacity = await self.controller.admission_capacity(
                    context.run_id, budget, context.global_budget
                )
                ready = tuple(kind for kind, state in states.items() if state == "READY")
                admitted, forecast = choose_admission(
                    ready,
                    capacity,
                    forecast_refund=(states["refund"] == "PENDING" and "invoice" in ready),
                )
                desired = [
                    AdmissionTask(
                        worker_id=self._identifiers(context.run_id, kind)[1],
                        task_id=self._identifiers(context.run_id, kind)[0],
                        kind=kind,
                        phase="ready" if kind in admitted else "forecast",
                        request=REQUESTS[kind],
                    )
                    for kind in (*admitted, *forecast)
                ]
                committed = await self.controller.reconcile_admission(
                    context.run_id,
                    desired,
                    states,
                    {kind: REQUESTS[kind].minimum_viable_tokens for kind in ready},
                    {kind: UTILITY[kind] for kind in ready},
                    budget,
                    context.global_budget,
                )
                if not committed:
                    raise ValueError("admission capacity changed during deterministic scheduling")
                if not admitted:
                    break
                kind = max(admitted, key=lambda item: (UTILITY[item], item))
                item = next(item for item in desired if item.kind == kind)
                goal, entities = objectives[kind]
                request = item.request
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
                    context,
                    item.task_id,
                    item.worker_id,
                    goal,
                    entities,
                    cap,
                    FutureWorkerFloor(),
                )
                await self.controller.activate_admitted_task(context.run_id, item, goal)
                attempted.add(kind)
                planned = _PlannedWorker(kind, item.task_id, item.worker_id, goal, cap, packet)
                try:
                    result = await self._run_one(planned, task, context, tools)
                except BaseException:
                    await self.controller.cancel_unspawned_elastic_task(
                        context.run_id, item.worker_id, item.task_id
                    )
                    raise
                results.append(result)
        finally:
            # Return all unstarted holds, including forecast reservations.
            await self.controller.reconcile_admission(
                context.run_id,
                (),
                last_states,
                {
                    kind: REQUESTS[kind].minimum_viable_tokens
                    for kind, state in last_states.items()
                    if state == "READY"
                },
                {kind: UTILITY[kind] for kind, state in last_states.items() if state == "READY"},
                budget,
                context.global_budget,
            )
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
