"""Run-scoped controller decisions serialized by PostgreSQL's run row lock."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.events import (
    ActionReservationAcquired,
    ActionReservationReleased,
    ControllerBudgetDeclared,
    ModelBudgetReserved,
    ModelBudgetSettled,
    ModelCallStarted,
    WorkerFinished,
    WorkerSpawned,
    WorkerStarted,
)
from offsecgym.schemas.specs import Budget
from offsecgym.storage.event_store import PostgresEventStore, RunTransaction
from offsecgym.storage.tables import (
    action_reservations,
    model_reservations,
    run_usage,
    worker_slots,
)


def request_fingerprint(action: ActionRequest) -> str:
    """Exact request identity including destination and canonical JSON body."""
    payload = [
        action.destination,
        action.method,
        action.path,
        str(action.identity_id) if action.identity_id else None,
        action.json_body,
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cost_microusd(value: float | None) -> int:
    return 0 if value is None else ceil(value * 1_000_000)


def budget_hash(budget: Budget) -> str:
    raw = json.dumps(budget.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PostgresControllerState:
    """Atomic budget, action, and worker state; each decision emits an event in its transaction."""

    def __init__(self, events: PostgresEventStore) -> None:
        self.events = events

    @staticmethod
    async def _usage(tx: RunTransaction, budget: Budget | None = None):
        await tx.connection.execute(
            pg_insert(run_usage)
            .values(
                run_id=tx.run_id,
                used_actions=0,
                used_http_requests=0,
                used_model_calls=0,
                used_tokens=0,
                reserved_tokens=0,
                used_cost_microusd=0,
                reserved_cost_microusd=0,
                spawned_workers=0,
                active_workers=0,
            )
            .on_conflict_do_nothing(index_elements=[run_usage.c.run_id])
        )
        usage = (
            (await tx.connection.execute(select(run_usage).where(run_usage.c.run_id == tx.run_id)))
            .mappings()
            .one()
        )
        if budget is not None:
            expected_hash = budget_hash(budget)
            if usage["budget_hash"] is None:
                await tx.connection.execute(
                    update(run_usage)
                    .where(run_usage.c.run_id == tx.run_id)
                    .values(budget_hash=expected_hash)
                )
                await tx.append(
                    ControllerBudgetDeclared(run_id=tx.run_id, actor="controller", budget=budget)
                )
                usage = (
                    (
                        await tx.connection.execute(
                            select(run_usage).where(run_usage.c.run_id == tx.run_id)
                        )
                    )
                    .mappings()
                    .one()
                )
            elif usage["budget_hash"] != expected_hash:
                raise ValueError("run budget differs from the declared global budget")
        return usage

    async def snapshot(self, run_id: UUID) -> dict[str, object]:
        async with self.events.run_transaction(run_id) as tx:
            return dict(await self._usage(tx))

    async def reserve_action(
        self, action: ActionRequest, budget: Budget, *, min_interval_seconds: float = 0
    ) -> str | None:
        fingerprint = request_fingerprint(action)
        async with self.events.run_transaction(action.run_id) as tx:
            usage = await self._usage(tx, budget)
            if (
                await tx.connection.execute(
                    select(action_reservations.c.action_id).where(
                        action_reservations.c.run_id == action.run_id,
                        action_reservations.c.action_id == action.action_id,
                    )
                )
            ).first():
                return "duplicate_action"
            if budget.max_actions is not None and usage["used_actions"] >= budget.max_actions:
                return "action_budget_exhausted"
            if (
                budget.max_http_requests is not None
                and usage["used_http_requests"] >= budget.max_http_requests
            ):
                return "http_budget_exhausted"
            now = datetime.now(UTC)
            last = usage["last_dispatch_at"]
            if last is not None and now - last < timedelta(seconds=min_interval_seconds):
                return "rate_limited"
            active = (
                await tx.connection.execute(
                    select(action_reservations.c.action_id).where(
                        action_reservations.c.run_id == action.run_id,
                        action_reservations.c.fingerprint == fingerprint,
                        action_reservations.c.released_at.is_(None),
                    )
                )
            ).first()
            if active:
                return "action_already_reserved"
            await tx.connection.execute(
                insert(action_reservations).values(
                    run_id=action.run_id,
                    action_id=action.action_id,
                    fingerprint=fingerprint,
                    worker_id=action.worker_id,
                    task_id=action.task_id,
                )
            )
            await tx.connection.execute(
                update(run_usage)
                .where(run_usage.c.run_id == action.run_id)
                .values(
                    used_actions=usage["used_actions"] + 1,
                    used_http_requests=usage["used_http_requests"] + 1,
                    last_dispatch_at=now,
                )
            )
            await tx.append(
                ActionReservationAcquired(
                    run_id=action.run_id,
                    actor="controller",
                    occurred_at=now,
                    action_id=action.action_id,
                    fingerprint=fingerprint,
                    worker_id=action.worker_id,
                    task_id=action.task_id,
                )
            )
        return None

    async def release_action(self, action: ActionRequest) -> None:
        async with self.events.run_transaction(action.run_id) as tx:
            row = (
                (
                    await tx.connection.execute(
                        select(action_reservations).where(
                            action_reservations.c.run_id == action.run_id,
                            action_reservations.c.action_id == action.action_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or row["released_at"] is not None
                or row["fingerprint"] != request_fingerprint(action)
                or row["worker_id"] != action.worker_id
                or row["task_id"] != action.task_id
            ):
                raise ValueError("action reservation is absent or already released")
            await tx.connection.execute(
                update(action_reservations)
                .where(
                    action_reservations.c.run_id == action.run_id,
                    action_reservations.c.action_id == action.action_id,
                )
                .values(released_at=datetime.now(UTC))
            )
            await tx.append(
                ActionReservationReleased(
                    run_id=action.run_id,
                    actor="controller",
                    action_id=action.action_id,
                    fingerprint=row["fingerprint"],
                    worker_id=action.worker_id,
                    task_id=action.task_id,
                )
            )

    async def reserve_model_call(
        self,
        run_id: UUID,
        call_id: UUID,
        budget: Budget,
        *,
        estimated_tokens: int,
        estimated_cost_microusd: int = 0,
        worker_id: UUID | None = None,
        task_id: UUID | None = None,
        started_event: ModelCallStarted | None = None,
    ) -> str | None:
        if estimated_tokens < 0 or estimated_cost_microusd < 0:
            raise ValueError("model reservation cannot be negative")
        if worker_id is not None and task_id is None:
            raise ValueError("worker model reservation requires task_id")
        async with self.events.run_transaction(run_id) as tx:
            usage = await self._usage(tx, budget)
            if (
                await tx.connection.execute(
                    select(model_reservations.c.call_id).where(
                        model_reservations.c.run_id == run_id,
                        model_reservations.c.call_id == call_id,
                    )
                )
            ).first():
                return "duplicate_model_call"
            if (
                budget.max_model_calls is not None
                and usage["used_model_calls"] >= budget.max_model_calls
            ):
                return "model_call_budget_exhausted"
            if (
                budget.max_total_tokens is not None
                and usage["used_tokens"] + usage["reserved_tokens"] + estimated_tokens
                > budget.max_total_tokens
            ):
                return "model_token_budget_exhausted"
            cost_limit = cost_microusd(budget.max_cost_usd)
            if budget.max_cost_usd is not None and (
                usage["used_cost_microusd"]
                + usage["reserved_cost_microusd"]
                + estimated_cost_microusd
                > cost_limit
            ):
                return "model_cost_budget_exhausted"
            await tx.connection.execute(
                insert(model_reservations).values(
                    run_id=run_id,
                    call_id=call_id,
                    worker_id=worker_id,
                    task_id=task_id,
                    reserved_tokens=estimated_tokens,
                    reserved_cost_microusd=estimated_cost_microusd,
                )
            )
            await tx.connection.execute(
                update(run_usage)
                .where(run_usage.c.run_id == run_id)
                .values(
                    used_model_calls=usage["used_model_calls"] + 1,
                    reserved_tokens=usage["reserved_tokens"] + estimated_tokens,
                    reserved_cost_microusd=(
                        usage["reserved_cost_microusd"] + estimated_cost_microusd
                    ),
                )
            )
            await tx.append(
                ModelBudgetReserved(
                    run_id=run_id,
                    actor="controller",
                    call_id=call_id,
                    reserved_tokens=estimated_tokens,
                    reserved_cost_microusd=estimated_cost_microusd,
                    worker_id=worker_id,
                    task_id=task_id,
                )
            )
            if started_event is not None:
                if started_event.run_id != run_id or started_event.call_id != call_id:
                    raise ValueError("model-call start does not match reservation")
                await tx.append(started_event)
        return None

    async def settle_model_call(
        self,
        run_id: UUID,
        call_id: UUID,
        *,
        actual_tokens: int,
        actual_cost_microusd: int = 0,
        worker_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> None:
        if actual_tokens < 0 or actual_cost_microusd < 0:
            raise ValueError("actual model usage cannot be negative")
        async with self.events.run_transaction(run_id) as tx:
            usage = await self._usage(tx)
            row = (
                (
                    await tx.connection.execute(
                        select(model_reservations).where(
                            model_reservations.c.run_id == run_id,
                            model_reservations.c.call_id == call_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or row["settled_at"] is not None
                or row["worker_id"] != worker_id
                or row["task_id"] != task_id
            ):
                raise ValueError("model reservation is absent or already settled")
            await tx.connection.execute(
                update(model_reservations)
                .where(
                    model_reservations.c.run_id == run_id, model_reservations.c.call_id == call_id
                )
                .values(settled_at=datetime.now(UTC))
            )
            await tx.connection.execute(
                update(run_usage)
                .where(run_usage.c.run_id == run_id)
                .values(
                    used_tokens=usage["used_tokens"] + actual_tokens,
                    reserved_tokens=usage["reserved_tokens"] - row["reserved_tokens"],
                    used_cost_microusd=usage["used_cost_microusd"] + actual_cost_microusd,
                    reserved_cost_microusd=(
                        usage["reserved_cost_microusd"] - row["reserved_cost_microusd"]
                    ),
                )
            )
            await tx.append(
                ModelBudgetSettled(
                    run_id=run_id,
                    actor="controller",
                    call_id=call_id,
                    actual_tokens=actual_tokens,
                    actual_cost_microusd=actual_cost_microusd,
                    worker_id=worker_id,
                    task_id=task_id,
                )
            )

    async def spawn_worker(
        self, run_id: UUID, worker_id: UUID, task_id: UUID, objective: str, budget: Budget
    ) -> str | None:
        async with self.events.run_transaction(run_id) as tx:
            usage = await self._usage(tx, budget)
            if budget.max_workers is not None and usage["spawned_workers"] >= budget.max_workers:
                return "worker_budget_exhausted"
            if (
                await tx.connection.execute(
                    select(worker_slots.c.worker_id).where(
                        worker_slots.c.run_id == run_id, worker_slots.c.worker_id == worker_id
                    )
                )
            ).first():
                return "duplicate_worker"
            await tx.connection.execute(
                insert(worker_slots).values(
                    run_id=run_id, worker_id=worker_id, task_id=task_id, status="spawned"
                )
            )
            await tx.connection.execute(
                update(run_usage)
                .where(run_usage.c.run_id == run_id)
                .values(spawned_workers=usage["spawned_workers"] + 1)
            )
            await tx.append(
                WorkerSpawned(
                    run_id=run_id,
                    actor="coordinator",
                    worker_id=worker_id,
                    task_id=task_id,
                    objective=objective,
                )
            )
        return None

    async def start_worker(
        self, run_id: UUID, worker_id: UUID, task_id: UUID, budget: Budget
    ) -> str | None:
        async with self.events.run_transaction(run_id) as tx:
            usage = await self._usage(tx, budget)
            row = (
                (
                    await tx.connection.execute(
                        select(worker_slots).where(
                            worker_slots.c.run_id == run_id, worker_slots.c.worker_id == worker_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["task_id"] != task_id or row["status"] != "spawned":
                return "worker_not_spawned"
            if (
                budget.max_concurrency is not None
                and usage["active_workers"] >= budget.max_concurrency
            ):
                return "worker_slots_exhausted"
            await tx.connection.execute(
                update(worker_slots)
                .where(worker_slots.c.run_id == run_id, worker_slots.c.worker_id == worker_id)
                .values(status="started")
            )
            await tx.connection.execute(
                update(run_usage)
                .where(run_usage.c.run_id == run_id)
                .values(active_workers=usage["active_workers"] + 1)
            )
            await tx.append(
                WorkerStarted(
                    run_id=run_id, actor="coordinator", worker_id=worker_id, task_id=task_id
                )
            )
        return None

    async def finish_worker(
        self,
        run_id: UUID,
        worker_id: UUID,
        task_id: UUID,
        status: str,
    ) -> None:
        if status not in {"completed", "budget_exhausted", "failed", "cancelled"}:
            raise ValueError("invalid worker finish status")
        async with self.events.run_transaction(run_id) as tx:
            usage = await self._usage(tx)
            row = (
                (
                    await tx.connection.execute(
                        select(worker_slots).where(
                            worker_slots.c.run_id == run_id, worker_slots.c.worker_id == worker_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["task_id"] != task_id or row["status"] != "started":
                raise ValueError("worker is absent, finished, or not started")
            await tx.connection.execute(
                update(worker_slots)
                .where(worker_slots.c.run_id == run_id, worker_slots.c.worker_id == worker_id)
                .values(status="finished")
            )
            await tx.connection.execute(
                update(run_usage)
                .where(run_usage.c.run_id == run_id)
                .values(active_workers=usage["active_workers"] - 1)
            )
            await tx.append(
                WorkerFinished(
                    run_id=run_id,
                    actor="coordinator",
                    worker_id=worker_id,
                    task_id=task_id,
                    status=status,
                )
            )


class LocalActionReservations:
    """Single-process adapter for small unit tests; production uses PostgreSQL."""

    def __init__(self, events) -> None:
        self.events = events
        self.lock = asyncio.Lock()
        self.usage: dict[UUID, dict[str, object]] = {}
        self.action_ids: set[tuple[UUID, UUID]] = set()
        self.active: dict[tuple[UUID, str], UUID] = {}

    async def reserve_action(
        self, action: ActionRequest, budget: Budget, *, min_interval_seconds: float = 0
    ) -> str | None:
        async with self.lock:
            usage = self.usage.setdefault(
                action.run_id,
                {"used_actions": 0, "used_http_requests": 0, "last_dispatch_at": None},
            )
            if (action.run_id, action.action_id) in self.action_ids:
                return "duplicate_action"
            if budget.max_actions is not None and usage["used_actions"] >= budget.max_actions:
                return "action_budget_exhausted"
            if (
                budget.max_http_requests is not None
                and usage["used_http_requests"] >= budget.max_http_requests
            ):
                return "http_budget_exhausted"
            now = datetime.now(UTC)
            last = usage["last_dispatch_at"]
            if last is not None and now - last < timedelta(seconds=min_interval_seconds):
                return "rate_limited"
            fingerprint = request_fingerprint(action)
            if (action.run_id, fingerprint) in self.active:
                return "action_already_reserved"
            self.action_ids.add((action.run_id, action.action_id))
            self.active[(action.run_id, fingerprint)] = action.action_id
            usage["used_actions"] += 1
            usage["used_http_requests"] += 1
            usage["last_dispatch_at"] = now
            await self.events.append(
                ActionReservationAcquired(
                    run_id=action.run_id,
                    actor="controller",
                    occurred_at=now,
                    action_id=action.action_id,
                    fingerprint=fingerprint,
                    worker_id=action.worker_id,
                    task_id=action.task_id,
                )
            )
            return None

    async def release_action(self, action: ActionRequest) -> None:
        async with self.lock:
            fingerprint = request_fingerprint(action)
            if self.active.get((action.run_id, fingerprint)) != action.action_id:
                raise ValueError("action reservation is absent or already released")
            del self.active[(action.run_id, fingerprint)]
            await self.events.append(
                ActionReservationReleased(
                    run_id=action.run_id,
                    actor="controller",
                    action_id=action.action_id,
                    fingerprint=fingerprint,
                    worker_id=action.worker_id,
                    task_id=action.task_id,
                )
            )
