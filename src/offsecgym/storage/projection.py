"""Rebuild M6 controller accounting and ownership from the event stream."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from offsecgym.schemas.events import (
    ActionAttemptReserved,
    ActionCompleted,
    ActionReservationAcquired,
    ActionReservationReleased,
    AnyTraceEvent,
    ControllerBudgetDeclared,
    CoverageLeaseAcquired,
    CoverageLeaseReleased,
    ModelBudgetReserved,
    ModelBudgetSettled,
    ModelReservationRejected,
    SchedulerDecision,
    TaskBudgetDenied,
    TaskBudgetExtended,
    TaskBudgetGranted,
    TaskBudgetReleased,
    WorkerBlocked,
    WorkerBudgetEscrowDeclared,
    WorkerContractViolated,
    WorkerDebriefed,
    WorkerFinished,
    WorkerHeartbeat,
    WorkerLeaseRecovered,
    WorkerObjectiveAction,
    WorkerOriented,
    WorkerPacketPrepared,
    WorkerScheduled,
    WorkerSpawned,
    WorkerStarted,
)
from offsecgym.schemas.scheduler import TaskBudgetRequest
from offsecgym.schemas.specs import Budget


@dataclass
class WorkerEscrowProjection:
    task_id: UUID
    token_limit: int
    model_call_limit: int
    action_limit: int
    http_limit: int
    elastic: bool = False
    kind: str | None = None
    request: TaskBudgetRequest | None = None
    used_tokens: int = 0
    reserved_tokens: int = 0
    used_model_calls: int = 0
    used_actions: int = 0
    used_http_requests: int = 0
    used_cost_microusd: int = 0
    reserved_cost_microusd: int = 0


@dataclass
class ControllerProjection:
    budget: Budget | None = None
    used_actions: int = 0
    used_http_requests: int = 0
    used_model_calls: int = 0
    used_tokens: int = 0
    reserved_tokens: int = 0
    used_cost_microusd: int = 0
    reserved_cost_microusd: int = 0
    spawned_workers: int = 0
    active_workers: int = 0
    last_dispatch_at: datetime | None = None
    action_owners: dict[UUID, tuple[UUID | None, UUID | None]] = field(default_factory=dict)
    completed_actions: set[UUID] = field(default_factory=set)
    active_actions: dict[str, UUID] = field(default_factory=dict)
    active_coverage: dict[UUID, UUID] = field(default_factory=dict)
    coverage_keys: dict[UUID, tuple[str, str]] = field(default_factory=dict)
    worker_status: dict[UUID, str] = field(default_factory=dict)
    worker_tasks: dict[UUID, UUID] = field(default_factory=dict)
    worker_packets: dict[UUID, WorkerPacketPrepared] = field(default_factory=dict)
    worker_scheduled: dict[UUID, WorkerScheduled] = field(default_factory=dict)
    worker_debriefs: dict[UUID, WorkerDebriefed] = field(default_factory=dict)
    worker_leases: dict[UUID, datetime] = field(default_factory=dict)
    recovered_workers: set[UUID] = field(default_factory=set)
    worker_outcomes: dict[UUID, str] = field(default_factory=dict)
    oriented_workers: set[UUID] = field(default_factory=set)
    model_reservations: dict[UUID, tuple[int, int, UUID | None, UUID | None, int | None]] = field(
        default_factory=dict
    )
    worker_escrows: dict[UUID, WorkerEscrowProjection] = field(default_factory=dict)
    scheduler_decisions: list[SchedulerDecision] = field(default_factory=list)
    model_rejections: list[ModelReservationRejected] = field(default_factory=list)
    budget_denials: list[TaskBudgetDenied] = field(default_factory=list)


def _escrow_for(
    state: ControllerProjection, worker_id: UUID | None, task_id: UUID | None
) -> WorkerEscrowProjection | None:
    if worker_id is None or not state.worker_escrows:
        return None
    account = state.worker_escrows.get(worker_id)
    if account is None or account.task_id != task_id:
        raise ValueError("worker event has no matching escrow account")
    return account


def project_controller_events(trace: Sequence[AnyTraceEvent]) -> ControllerProjection:
    state = ControllerProjection()
    for event in trace:
        if isinstance(event, ControllerBudgetDeclared):
            if state.budget is not None:
                raise ValueError("run budget declared more than once")
            state.budget = event.budget
        elif isinstance(event, WorkerBudgetEscrowDeclared):
            if event.worker_id in state.worker_escrows or state.spawned_workers:
                raise ValueError("worker escrow declared after spawn or duplicated")
            if state.budget is None:
                raise ValueError("worker escrow has no declared global budget")
            state.worker_escrows[event.worker_id] = WorkerEscrowProjection(
                task_id=event.task_id,
                token_limit=event.budget.max_total_tokens,
                model_call_limit=event.budget.max_model_calls,
                action_limit=event.budget.max_actions,
                http_limit=event.budget.max_http_requests,
            )
            for field, ceiling_name in (
                ("token_limit", "max_total_tokens"),
                ("model_call_limit", "max_model_calls"),
                ("action_limit", "max_actions"),
                ("http_limit", "max_http_requests"),
            ):
                ceiling = getattr(state.budget, ceiling_name)
                if (
                    ceiling is not None
                    and sum(getattr(account, field) for account in state.worker_escrows.values())
                    > ceiling
                ):
                    raise ValueError("worker escrow declarations exceed global budget")
        elif isinstance(event, SchedulerDecision):
            state.scheduler_decisions.append(event)
        elif isinstance(event, TaskBudgetDenied):
            state.budget_denials.append(event)
        elif isinstance(event, TaskBudgetGranted):
            if event.worker_id in state.worker_escrows or any(
                account.elastic and account.kind == event.kind
                for account in state.worker_escrows.values()
            ):
                raise ValueError("duplicate elastic task grant")
            state.worker_escrows[event.worker_id] = WorkerEscrowProjection(
                task_id=event.task_id,
                token_limit=event.token_limit,
                model_call_limit=event.model_call_limit,
                action_limit=event.action_limit,
                http_limit=event.http_limit,
                elastic=True,
                kind=event.kind,
                request=event.request,
            )
        elif isinstance(event, TaskBudgetExtended):
            account = _escrow_for(state, event.worker_id, event.task_id)
            if (
                account is None
                or not account.elastic
                or account.request is None
                or (
                    account.token_limit != event.prior_token_limit
                    or account.model_call_limit != event.prior_model_call_limit
                    or event.token_limit < account.token_limit
                    or event.model_call_limit < account.model_call_limit
                    or event.token_limit > account.request.max_tokens
                    or event.model_call_limit > account.request.max_model_calls
                )
            ):
                raise ValueError("invalid elastic task extension")
            account.token_limit = event.token_limit
            account.model_call_limit = event.model_call_limit
        elif isinstance(event, TaskBudgetReleased):
            account = _escrow_for(state, event.worker_id, event.task_id)
            if (
                account is None
                or not account.elastic
                or (
                    account.token_limit - event.released_tokens != event.final_token_limit
                    or account.model_call_limit - event.released_model_calls
                    != event.final_model_call_limit
                    or account.action_limit - event.released_actions != event.final_action_limit
                    or account.http_limit - event.released_http_requests != event.final_http_limit
                )
            ):
                raise ValueError("invalid elastic task release")
            account.token_limit = event.final_token_limit
            account.model_call_limit = event.final_model_call_limit
            account.action_limit = event.final_action_limit
            account.http_limit = event.final_http_limit
        elif isinstance(event, ModelReservationRejected):
            state.model_rejections.append(event)
        elif isinstance(event, ActionAttemptReserved):
            if event.action_id in state.action_owners:
                raise ValueError("duplicate action attempt in event stream")
            state.used_actions += 1
            state.action_owners[event.action_id] = (event.worker_id, event.task_id)
            escrow = _escrow_for(state, event.worker_id, event.task_id)
            if escrow is not None:
                escrow.used_actions += 1
                if escrow.used_actions > escrow.action_limit:
                    raise ValueError("worker action escrow exceeded")
        elif isinstance(event, ActionReservationAcquired):
            if event.fingerprint in state.active_actions:
                raise ValueError("duplicate active action reservation in event stream")
            if state.action_owners.get(event.action_id) != (event.worker_id, event.task_id):
                raise ValueError("action dispatch has no matching attributed attempt")
            state.used_http_requests += 1
            escrow = _escrow_for(state, event.worker_id, event.task_id)
            if escrow is not None:
                escrow.used_http_requests += 1
                if escrow.used_http_requests > escrow.http_limit:
                    raise ValueError("worker HTTP escrow exceeded")
            state.active_actions[event.fingerprint] = event.action_id
            state.last_dispatch_at = event.occurred_at
        elif isinstance(event, ActionReservationReleased):
            if state.active_actions.get(event.fingerprint) != event.action_id:
                raise ValueError("action release has no matching active reservation")
            del state.active_actions[event.fingerprint]
        elif isinstance(event, ActionCompleted):
            state.completed_actions.add(event.action_id)
        elif isinstance(event, CoverageLeaseAcquired):
            if event.claim_id in state.active_coverage:
                raise ValueError("duplicate active coverage lease")
            key = (event.component.casefold(), event.objective.casefold())
            if key in state.coverage_keys.values():
                raise ValueError("duplicate active coverage objective")
            state.active_coverage[event.claim_id] = event.task_id
            state.coverage_keys[event.claim_id] = key
        elif isinstance(event, CoverageLeaseReleased):
            if state.active_coverage.get(event.claim_id) != event.task_id:
                raise ValueError("coverage release has no matching active lease")
            del state.active_coverage[event.claim_id]
            del state.coverage_keys[event.claim_id]
        elif isinstance(event, ModelBudgetReserved):
            if event.call_id in state.model_reservations:
                raise ValueError("duplicate model budget reservation")
            state.used_model_calls += 1
            state.reserved_tokens += event.reserved_tokens
            state.reserved_cost_microusd += event.reserved_cost_microusd
            escrow = _escrow_for(state, event.worker_id, event.task_id)
            if escrow is not None:
                escrow.used_model_calls += 1
                escrow.reserved_tokens += event.reserved_tokens
                escrow.reserved_cost_microusd += event.reserved_cost_microusd
                if escrow.used_model_calls > escrow.model_call_limit or (
                    escrow.used_tokens + escrow.reserved_tokens > escrow.token_limit
                ):
                    raise ValueError("worker model escrow exceeded")
            state.model_reservations[event.call_id] = (
                event.reserved_tokens,
                event.reserved_cost_microusd,
                event.worker_id,
                event.task_id,
                event.reserved_input_tokens,
            )
        elif isinstance(event, ModelBudgetSettled):
            reservation = state.model_reservations.pop(event.call_id, None)
            if reservation is None:
                raise ValueError("model settlement has no matching reservation")
            if reservation[2:4] != (event.worker_id, event.task_id):
                raise ValueError("model settlement owner differs from reservation")
            if event.schema_version == "2" and event.reservation_error != (
                event.actual_tokens - reservation[0]
            ):
                raise ValueError("model settlement reservation error differs from reservation")
            if event.schema_version == "2":
                if reservation[4] is None or event.input_reservation_error != (
                    event.actual_input_tokens - reservation[4]
                ):
                    raise ValueError("model input reservation error differs from reservation")
            state.reserved_tokens -= reservation[0]
            state.reserved_cost_microusd -= reservation[1]
            state.used_tokens += event.actual_tokens
            state.used_cost_microusd += event.actual_cost_microusd
            escrow = _escrow_for(state, event.worker_id, event.task_id)
            if escrow is not None:
                escrow.reserved_tokens -= reservation[0]
                escrow.reserved_cost_microusd -= reservation[1]
                escrow.used_tokens += event.actual_tokens
                escrow.used_cost_microusd += event.actual_cost_microusd
                if escrow.used_tokens + escrow.reserved_tokens > escrow.token_limit:
                    raise ValueError("worker actual token usage exceeded escrow")
        elif isinstance(event, WorkerScheduled):
            if event.worker_id in state.worker_scheduled:
                raise ValueError("worker scheduled more than once")
            state.worker_scheduled[event.worker_id] = event
        elif isinstance(event, WorkerSpawned):
            if event.worker_id in state.worker_status:
                raise ValueError("duplicate worker spawn")
            scheduled = state.worker_scheduled.get(event.worker_id)
            if scheduled is not None and scheduled.task_id != event.task_id:
                raise ValueError("scheduled worker has different task ownership")
            state.spawned_workers += 1
            state.worker_status[event.worker_id] = "spawned"
            state.worker_tasks[event.worker_id] = event.task_id
            if event.lease_expires_at is not None:
                state.worker_leases[event.worker_id] = event.lease_expires_at
        elif isinstance(event, WorkerPacketPrepared):
            if (
                state.worker_status.get(event.worker_id) != "spawned"
                or state.worker_tasks.get(event.worker_id) != event.task_id
                or event.worker_id in state.worker_packets
            ):
                raise ValueError("worker packet has no matching spawn or is duplicated")
            state.worker_packets[event.worker_id] = event
        elif isinstance(event, WorkerStarted):
            if (
                state.worker_status.get(event.worker_id) != "spawned"
                or state.worker_tasks.get(event.worker_id) != event.task_id
            ):
                raise ValueError("worker start has no matching spawn")
            state.active_workers += 1
            state.worker_status[event.worker_id] = "started"
            if event.lease_expires_at is not None:
                state.worker_leases[event.worker_id] = event.lease_expires_at
        elif isinstance(event, WorkerHeartbeat):
            if (
                state.worker_status.get(event.worker_id) != "started"
                or state.worker_tasks.get(event.worker_id) != event.task_id
            ):
                raise ValueError("worker heartbeat has no matching active worker")
            state.worker_leases[event.worker_id] = event.lease_expires_at
        elif isinstance(event, WorkerLeaseRecovered):
            if (
                state.worker_status.get(event.worker_id) not in ("spawned", "started")
                or state.worker_tasks.get(event.worker_id) != event.task_id
                or event.worker_id in state.recovered_workers
            ):
                raise ValueError("worker recovery has no matching active worker")
            state.recovered_workers.add(event.worker_id)
        elif isinstance(event, WorkerOriented):
            if (
                state.worker_status.get(event.worker_id) != "started"
                or event.worker_id in state.oriented_workers
            ):
                raise ValueError(
                    "worker orientation has no matching active worker or is duplicated"
                )
            state.oriented_workers.add(event.worker_id)
        elif isinstance(event, (WorkerObjectiveAction, WorkerBlocked, WorkerContractViolated)):
            if state.worker_status.get(event.worker_id) != "started":
                raise ValueError("worker outcome has no matching active worker")
            if state.worker_tasks[event.worker_id] != event.task_id:
                raise ValueError("worker outcome has different task ownership")
            if isinstance(event, WorkerObjectiveAction) and (
                event.action_id not in state.completed_actions
                or state.action_owners.get(event.action_id) != (event.worker_id, event.task_id)
            ):
                raise ValueError("worker objective action has no completed owned action")
            if event.worker_id in state.worker_outcomes:
                if not (
                    isinstance(event, WorkerObjectiveAction)
                    and state.worker_outcomes[event.worker_id] == "objective_action"
                ):
                    raise ValueError("worker has conflicting terminal outcomes")
            state.worker_outcomes[event.worker_id] = (
                "objective_action"
                if isinstance(event, WorkerObjectiveAction)
                else "blocked"
                if isinstance(event, WorkerBlocked)
                else "contract_violated"
            )
        elif isinstance(event, WorkerDebriefed):
            if (
                state.worker_status.get(event.worker_id) not in ("started", "spawned")
                or state.worker_tasks.get(event.worker_id) != event.task_id
                or event.worker_id in state.worker_debriefs
            ):
                raise ValueError("worker debrief has no matching start or is duplicated")
            state.worker_debriefs[event.worker_id] = event
        elif isinstance(event, WorkerFinished):
            if (
                state.worker_status.get(event.worker_id) not in ("started", "spawned")
                or (
                    state.worker_status.get(event.worker_id) == "spawned"
                    and event.status != "failed"
                )
                or state.worker_tasks.get(event.worker_id) != event.task_id
            ):
                raise ValueError("worker finish has no matching start")
            if (
                event.worker_id in state.worker_packets
                and event.worker_id not in state.worker_debriefs
            ):
                raise ValueError("packet-bearing worker finished without debrief")
            packet_event = state.worker_packets.get(event.worker_id)
            if (
                packet_event is not None
                and packet_event.packet.contract is not None
                and event.status != "cancelled"
                and not (
                    state.worker_status[event.worker_id] == "spawned"
                    and event.worker_id in state.recovered_workers
                )
            ):
                outcome = state.worker_outcomes.get(event.worker_id)
                if outcome is None:
                    raise ValueError("contract worker finished without objective outcome")
                if outcome == "contract_violated" and event.status != "failed":
                    raise ValueError("worker finish status conflicts with contract outcome")
            if state.worker_status[event.worker_id] == "started":
                state.active_workers -= 1
            state.worker_status[event.worker_id] = "finished"
            state.worker_leases.pop(event.worker_id, None)
        if state.budget is not None:
            for limit, consumed, outstanding in (
                (
                    state.budget.max_total_tokens,
                    state.used_tokens + state.reserved_tokens,
                    sum(
                        account.token_limit - account.used_tokens - account.reserved_tokens
                        for account in state.worker_escrows.values()
                    ),
                ),
                (
                    state.budget.max_model_calls,
                    state.used_model_calls,
                    sum(
                        account.model_call_limit - account.used_model_calls
                        for account in state.worker_escrows.values()
                    ),
                ),
                (
                    state.budget.max_actions,
                    state.used_actions,
                    sum(
                        account.action_limit - account.used_actions
                        for account in state.worker_escrows.values()
                    ),
                ),
                (
                    state.budget.max_http_requests,
                    state.used_http_requests,
                    sum(
                        account.http_limit - account.used_http_requests
                        for account in state.worker_escrows.values()
                    ),
                ),
            ):
                if outstanding < 0 or (limit is not None and consumed + outstanding > limit):
                    raise ValueError("event replay exceeds conserved controller budget")
    return state
