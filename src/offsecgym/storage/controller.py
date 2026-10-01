"""Run-scoped controller decisions serialized by PostgreSQL's run row lock."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.events import (
    ActionAttemptReserved,
    ActionBlocked,
    ActionCompleted,
    ActionFailed,
    ActionRequested,
    ActionReservationAcquired,
    ActionReservationReleased,
    ControllerBudgetDeclared,
    CoverageLeaseReleased,
    CoverageUpdated,
    ModelBudgetReserved,
    ModelBudgetSettled,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    WorkerBlocked,
    WorkerBudgetEscrowDeclared,
    WorkerContractViolated,
    WorkerDebriefed,
    WorkerFinished,
    WorkerHeartbeat,
    WorkerLeaseRecovered,
    WorkerObjectiveAction,
    WorkerPacketPrepared,
    WorkerSpawned,
    WorkerStarted,
)
from offsecgym.schemas.specs import Budget
from offsecgym.storage.event_store import PostgresEventStore, RunTransaction
from offsecgym.storage.tables import (
    action_reservations,
    model_reservations,
    run_usage,
    worker_budget_accounts,
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

    WORKER_LEASE_SECONDS = 45
    WORKER_HEARTBEAT_SECONDS = 10

    @staticmethod
    def _worker_lock_key(run_id: UUID, worker_id: UUID) -> int:
        digest = hashlib.sha256(b"offsecgym-worker" + run_id.bytes + worker_id.bytes).digest()
        return int.from_bytes(digest[:8], "big", signed=True)

    @asynccontextmanager
    async def worker_guard(self, run_id: UUID, worker_id: UUID, *, blocking: bool = True):
        """Hold a PostgreSQL session lock for one worker; process death releases it."""
        engine = create_async_engine(self.events.engine.url, poolclass=NullPool)
        connection = await engine.connect()
        key = self._worker_lock_key(run_id, worker_id)
        try:
            lock_function = "pg_advisory_lock" if blocking else "pg_try_advisory_lock"
            acquired = (
                await connection.execute(sql_text(f"SELECT {lock_function}(:key)"), {"key": key})
            ).scalar_one()
            await connection.commit()
            try:
                yield acquired is not False
            finally:
                if acquired is not False:
                    await connection.execute(
                        sql_text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    )
                    await connection.commit()
        finally:
            await connection.close()
            await engine.dispose()

    async def heartbeat_worker(
        self, run_id: UUID, worker_id: UUID, task_id: UUID, *, now: datetime | None = None
    ) -> datetime:
        now = now or datetime.now(UTC)
        expires = now + timedelta(seconds=self.WORKER_LEASE_SECONDS)
        async with self.events.run_transaction(run_id) as tx:
            row = (
                (
                    await tx.connection.execute(
                        select(worker_slots).where(
                            worker_slots.c.run_id == run_id,
                            worker_slots.c.worker_id == worker_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["task_id"] != task_id or row["status"] != "started":
                raise ValueError("worker lease is no longer active")
            await tx.connection.execute(
                update(worker_slots)
                .where(worker_slots.c.run_id == run_id, worker_slots.c.worker_id == worker_id)
                .values(last_heartbeat_at=now, lease_expires_at=expires)
            )
            await tx.append(
                WorkerHeartbeat(
                    run_id=run_id,
                    actor="controller",
                    worker_id=worker_id,
                    task_id=task_id,
                    occurred_at=now,
                    lease_expires_at=expires,
                )
            )
        return expires

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

    async def worker_escrow_snapshot(self, run_id: UUID) -> list[dict[str, object]]:
        async with self.events.run_transaction(run_id) as tx:
            rows = await tx.connection.execute(
                select(worker_budget_accounts).where(worker_budget_accounts.c.run_id == run_id)
            )
            return [dict(row) for row in rows.mappings()]

    @staticmethod
    async def _worker_escrow(tx: RunTransaction, worker_id: UUID | None, task_id: UUID | None):
        if worker_id is None:
            return None
        row = (
            (
                await tx.connection.execute(
                    select(worker_budget_accounts).where(
                        worker_budget_accounts.c.run_id == tx.run_id,
                        worker_budget_accounts.c.worker_id == worker_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            if row["task_id"] != task_id:
                raise ValueError("worker escrow task ownership mismatch")
            return row
        enabled = (
            await tx.connection.execute(
                select(worker_budget_accounts.c.worker_id)
                .where(worker_budget_accounts.c.run_id == tx.run_id)
                .limit(1)
            )
        ).first()
        if enabled:
            raise ValueError("worker has no declared escrow account")
        return None

    async def declare_worker_escrows(
        self,
        run_id: UUID,
        planned: Sequence[tuple[UUID, UUID, str, Budget]],
        worker_budget: Budget,
        global_budget: Budget,
    ) -> None:
        if not planned or len({item[0] for item in planned}) != len(planned):
            raise ValueError("worker escrows require distinct worker IDs")
        if len({item[1] for item in planned}) != len(planned):
            raise ValueError("worker escrows require distinct task IDs")
        fields = (
            ("max_total_tokens", "max_total_tokens"),
            ("max_model_calls", "max_model_calls"),
            ("max_actions", "max_actions"),
            ("max_http_requests", "max_http_requests"),
        )
        for field, ceiling in fields:
            limits = [getattr(item[3], field) for item in planned]
            worker_cap = getattr(worker_budget, ceiling)
            global_cap = getattr(global_budget, ceiling)
            if any(limit is None or limit <= 0 for limit in limits) or worker_cap is None:
                raise ValueError(f"worker escrow requires explicit positive {field}")
            if sum(limits) > worker_cap or (global_cap is not None and worker_cap > global_cap):
                raise ValueError(f"worker escrow {field} exceeds experiment budget")
        if worker_budget.max_cost_usd is not None:
            costs = [item[3].max_cost_usd for item in planned]
            if any(cost is None for cost in costs) or sum(
                cost_microusd(cost) for cost in costs
            ) > cost_microusd(worker_budget.max_cost_usd):
                raise ValueError("worker escrow cost exceeds experiment budget")
        async with self.events.run_transaction(run_id) as tx:
            usage = await self._usage(tx, global_budget)
            existing = (
                await tx.connection.execute(
                    select(worker_budget_accounts.c.worker_id)
                    .where(worker_budget_accounts.c.run_id == run_id)
                    .limit(1)
                )
            ).first()
            if existing or usage["spawned_workers"] or usage["used_model_calls"]:
                raise ValueError("worker escrows must be declared once before worker execution")
            if (
                global_budget.max_actions is not None
                and usage["used_actions"] + sum(item[3].max_actions for item in planned)
                > global_budget.max_actions
            ) or (
                global_budget.max_http_requests is not None
                and usage["used_http_requests"] + sum(item[3].max_http_requests for item in planned)
                > global_budget.max_http_requests
            ):
                raise ValueError("bootstrap use leaves insufficient worker action escrow")
            for worker_id, task_id, objective, slice_budget in planned:
                await tx.connection.execute(
                    insert(worker_budget_accounts).values(
                        run_id=run_id,
                        worker_id=worker_id,
                        task_id=task_id,
                        token_limit=slice_budget.max_total_tokens,
                        model_call_limit=slice_budget.max_model_calls,
                        action_limit=slice_budget.max_actions,
                        http_limit=slice_budget.max_http_requests,
                        cost_limit_microusd=(
                            cost_microusd(slice_budget.max_cost_usd)
                            if slice_budget.max_cost_usd is not None
                            else None
                        ),
                        used_tokens=0,
                        reserved_tokens=0,
                        used_model_calls=0,
                        used_actions=0,
                        used_http_requests=0,
                        used_cost_microusd=0,
                        reserved_cost_microusd=0,
                    )
                )
                await tx.append(
                    WorkerBudgetEscrowDeclared(
                        run_id=run_id,
                        actor="controller",
                        worker_id=worker_id,
                        task_id=task_id,
                        objective=objective,
                        budget=slice_budget,
                    )
                )

    @staticmethod
    async def _worker_slot_active(
        tx: RunTransaction, worker_id: UUID | None, task_id: UUID | None
    ) -> bool:
        if worker_id is None:
            return True
        row = (
            await tx.connection.execute(
                select(worker_slots.c.task_id, worker_slots.c.status).where(
                    worker_slots.c.run_id == tx.run_id,
                    worker_slots.c.worker_id == worker_id,
                )
            )
        ).one_or_none()
        if row is None:
            account = (
                await tx.connection.execute(
                    select(worker_budget_accounts.c.worker_id).where(
                        worker_budget_accounts.c.run_id == tx.run_id,
                        worker_budget_accounts.c.worker_id == worker_id,
                    )
                )
            ).first()
            return account is None
        return row.task_id == task_id and row.status == "started"

    async def reserve_request(
        self,
        action: ActionRequest,
        budget: Budget,
        requested_event: ActionRequested | None = None,
    ) -> str | None:
        if requested_event is not None and (
            requested_event.run_id != action.run_id or requested_event.action_id != action.action_id
        ):
            raise ValueError("action request event does not match reservation")
        async with self.events.run_transaction(action.run_id) as tx:
            usage = await self._usage(tx, budget)
            if not await self._worker_slot_active(tx, action.worker_id, action.task_id):
                return "worker_lease_expired"
            escrow = await self._worker_escrow(tx, action.worker_id, action.task_id)
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
            if escrow is not None and escrow["used_actions"] >= escrow["action_limit"]:
                return "worker_action_escrow_exhausted"
            await tx.connection.execute(
                insert(action_reservations).values(
                    run_id=action.run_id,
                    action_id=action.action_id,
                    fingerprint=request_fingerprint(action),
                    worker_id=action.worker_id,
                    task_id=action.task_id,
                    released_at=datetime.now(UTC),
                )
            )
            await tx.connection.execute(
                update(run_usage)
                .where(run_usage.c.run_id == action.run_id)
                .values(used_actions=usage["used_actions"] + 1)
            )
            if escrow is not None:
                await tx.connection.execute(
                    update(worker_budget_accounts)
                    .where(
                        worker_budget_accounts.c.run_id == action.run_id,
                        worker_budget_accounts.c.worker_id == action.worker_id,
                    )
                    .values(used_actions=escrow["used_actions"] + 1)
                )
            await tx.append(
                ActionAttemptReserved(
                    run_id=action.run_id,
                    actor="controller",
                    action_id=action.action_id,
                    worker_id=action.worker_id,
                    task_id=action.task_id,
                )
            )
            if requested_event is not None:
                await tx.append(requested_event)
        return None

    async def reserve_action(
        self,
        action: ActionRequest,
        budget: Budget,
        *,
        min_interval_seconds: float = 0,
        record_attempt: bool = True,
    ) -> str | None:
        if record_attempt:
            reason = await self.reserve_request(action, budget)
            if reason:
                return reason
        fingerprint = request_fingerprint(action)
        async with self.events.run_transaction(action.run_id) as tx:
            usage = await self._usage(tx, budget)
            if not await self._worker_slot_active(tx, action.worker_id, action.task_id):
                return "worker_lease_expired"
            escrow = await self._worker_escrow(tx, action.worker_id, action.task_id)
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
            if row is None or row["released_at"] is None:
                return "action_not_pending"
            if (
                row["fingerprint"] != fingerprint
                or row["worker_id"] != action.worker_id
                or row["task_id"] != action.task_id
            ):
                raise ValueError("action dispatch does not match reserved request")
            if (
                budget.max_http_requests is not None
                and usage["used_http_requests"] >= budget.max_http_requests
            ):
                return "http_budget_exhausted"
            if escrow is not None and escrow["used_http_requests"] >= escrow["http_limit"]:
                return "worker_http_escrow_exhausted"
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
                update(action_reservations)
                .where(
                    action_reservations.c.run_id == action.run_id,
                    action_reservations.c.action_id == action.action_id,
                )
                .values(released_at=None)
            )
            await tx.connection.execute(
                update(run_usage)
                .where(run_usage.c.run_id == action.run_id)
                .values(
                    used_http_requests=usage["used_http_requests"] + 1,
                    last_dispatch_at=now,
                )
            )
            if escrow is not None:
                await tx.connection.execute(
                    update(worker_budget_accounts)
                    .where(
                        worker_budget_accounts.c.run_id == action.run_id,
                        worker_budget_accounts.c.worker_id == action.worker_id,
                    )
                    .values(used_http_requests=escrow["used_http_requests"] + 1)
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
        reserved_input_tokens: int,
        reserved_output_tokens: int,
        request_bytes: int,
        estimated_cost_microusd: int = 0,
        protected_future_tokens: int = 0,
        protected_future_model_calls: int = 0,
        protected_future_cost_microusd: int = 0,
        worker_id: UUID | None = None,
        task_id: UUID | None = None,
        started_event: ModelCallStarted | None = None,
    ) -> str | None:
        if (
            min(
                reserved_input_tokens,
                reserved_output_tokens,
                request_bytes,
                estimated_cost_microusd,
                protected_future_tokens,
                protected_future_model_calls,
                protected_future_cost_microusd,
            )
            < 0
        ):
            raise ValueError("model reservation cannot be negative")
        estimated_tokens = reserved_input_tokens + reserved_output_tokens
        if worker_id is not None and task_id is None:
            raise ValueError("worker model reservation requires task_id")
        async with self.events.run_transaction(run_id) as tx:
            usage = await self._usage(tx, budget)
            if not await self._worker_slot_active(tx, worker_id, task_id):
                return "worker_lease_expired"
            escrow = await self._worker_escrow(tx, worker_id, task_id)
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
                and usage["used_model_calls"] + 1 + protected_future_model_calls
                > budget.max_model_calls
            ):
                return "model_call_budget_exhausted"
            if escrow is not None and escrow["used_model_calls"] >= escrow["model_call_limit"]:
                return "worker_model_call_escrow_exhausted"
            if (
                budget.max_total_tokens is not None
                and usage["used_tokens"]
                + usage["reserved_tokens"]
                + estimated_tokens
                + protected_future_tokens
                > budget.max_total_tokens
            ):
                return "model_token_budget_exhausted"
            if (
                escrow is not None
                and escrow["used_tokens"] + escrow["reserved_tokens"] + estimated_tokens
                > escrow["token_limit"]
            ):
                return "worker_token_escrow_exhausted"
            cost_limit = cost_microusd(budget.max_cost_usd)
            if budget.max_cost_usd is not None and (
                usage["used_cost_microusd"]
                + usage["reserved_cost_microusd"]
                + estimated_cost_microusd
                + protected_future_cost_microusd
                > cost_limit
            ):
                return "model_cost_budget_exhausted"
            if (
                escrow is not None
                and escrow["cost_limit_microusd"] is not None
                and escrow["used_cost_microusd"]
                + escrow["reserved_cost_microusd"]
                + estimated_cost_microusd
                > escrow["cost_limit_microusd"]
            ):
                return "worker_cost_escrow_exhausted"
            await tx.connection.execute(
                insert(model_reservations).values(
                    run_id=run_id,
                    call_id=call_id,
                    worker_id=worker_id,
                    task_id=task_id,
                    reserved_tokens=estimated_tokens,
                    reserved_input_tokens=reserved_input_tokens,
                    reserved_output_tokens=reserved_output_tokens,
                    request_bytes=request_bytes,
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
            if escrow is not None:
                await tx.connection.execute(
                    update(worker_budget_accounts)
                    .where(
                        worker_budget_accounts.c.run_id == run_id,
                        worker_budget_accounts.c.worker_id == worker_id,
                    )
                    .values(
                        used_model_calls=escrow["used_model_calls"] + 1,
                        reserved_tokens=escrow["reserved_tokens"] + estimated_tokens,
                        reserved_cost_microusd=(
                            escrow["reserved_cost_microusd"] + estimated_cost_microusd
                        ),
                    )
                )
            await tx.append(
                ModelBudgetReserved(
                    run_id=run_id,
                    actor="controller",
                    call_id=call_id,
                    reserved_tokens=estimated_tokens,
                    reserved_input_tokens=reserved_input_tokens,
                    reserved_output_tokens=reserved_output_tokens,
                    request_bytes=request_bytes,
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
        actual_input_tokens: int,
        actual_output_tokens: int,
        actual_cost_microusd: int = 0,
        worker_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> None:
        if min(actual_input_tokens, actual_output_tokens, actual_cost_microusd) < 0:
            raise ValueError("actual model usage cannot be negative")
        actual_tokens = actual_input_tokens + actual_output_tokens
        async with self.events.run_transaction(run_id) as tx:
            usage = await self._usage(tx)
            if not await self._worker_slot_active(tx, worker_id, task_id):
                raise ValueError("worker lease is no longer active")
            escrow = await self._worker_escrow(tx, worker_id, task_id)
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
            if escrow is not None:
                await tx.connection.execute(
                    update(worker_budget_accounts)
                    .where(
                        worker_budget_accounts.c.run_id == run_id,
                        worker_budget_accounts.c.worker_id == worker_id,
                    )
                    .values(
                        used_tokens=escrow["used_tokens"] + actual_tokens,
                        reserved_tokens=escrow["reserved_tokens"] - row["reserved_tokens"],
                        used_cost_microusd=escrow["used_cost_microusd"] + actual_cost_microusd,
                        reserved_cost_microusd=(
                            escrow["reserved_cost_microusd"] - row["reserved_cost_microusd"]
                        ),
                    )
                )
            await tx.append(
                ModelBudgetSettled(
                    schema_version=("2" if row["reserved_input_tokens"] is not None else "1"),
                    run_id=run_id,
                    actor="controller",
                    call_id=call_id,
                    actual_tokens=actual_tokens,
                    actual_input_tokens=actual_input_tokens,
                    actual_output_tokens=actual_output_tokens,
                    reservation_error=actual_tokens - row["reserved_tokens"],
                    input_reservation_error=(
                        actual_input_tokens - row["reserved_input_tokens"]
                        if row["reserved_input_tokens"] is not None
                        else None
                    ),
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
            await self._worker_escrow(tx, worker_id, task_id)
            now = datetime.now(UTC)
            expires = now + timedelta(seconds=self.WORKER_LEASE_SECONDS)
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
                    run_id=run_id,
                    worker_id=worker_id,
                    task_id=task_id,
                    status="spawned",
                    last_heartbeat_at=now,
                    lease_expires_at=expires,
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
                    lease_expires_at=expires,
                )
            )
        return None

    async def start_worker(
        self, run_id: UUID, worker_id: UUID, task_id: UUID, budget: Budget
    ) -> str | None:
        async with self.events.run_transaction(run_id) as tx:
            usage = await self._usage(tx, budget)
            now = datetime.now(UTC)
            expires = now + timedelta(seconds=self.WORKER_LEASE_SECONDS)
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
                .values(status="started", last_heartbeat_at=now, lease_expires_at=expires)
            )
            await tx.connection.execute(
                update(run_usage)
                .where(run_usage.c.run_id == run_id)
                .values(active_workers=usage["active_workers"] + 1)
            )
            await tx.append(
                WorkerStarted(
                    run_id=run_id,
                    actor="coordinator",
                    worker_id=worker_id,
                    task_id=task_id,
                    lease_expires_at=expires,
                )
            )
        return None

    async def abort_spawned_worker(
        self, run_id: UUID, worker_id: UUID, task_id: UUID, reason: str
    ) -> None:
        """Close a worker that failed during packet preparation before it started."""
        async with self.events.run_transaction(run_id) as tx:
            row = (
                (
                    await tx.connection.execute(
                        select(worker_slots).where(
                            worker_slots.c.run_id == run_id,
                            worker_slots.c.worker_id == worker_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["task_id"] != task_id or row["status"] != "spawned":
                raise ValueError("worker is not in spawned state")
            await tx.append(
                WorkerDebriefed(
                    run_id=run_id,
                    actor="controller",
                    worker_id=worker_id,
                    task_id=task_id,
                    open_questions=(reason,),
                )
            )
            await tx.connection.execute(
                update(worker_slots)
                .where(worker_slots.c.run_id == run_id, worker_slots.c.worker_id == worker_id)
                .values(status="finished", lease_expires_at=None)
            )
            await tx.append(
                WorkerFinished(
                    run_id=run_id,
                    actor="controller",
                    worker_id=worker_id,
                    task_id=task_id,
                    status="failed",
                )
            )

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

    async def reconcile_stale_workers(self, run_id: UUID, *, now: datetime | None = None) -> int:
        """Close crashed workers only after their lease expired and session lock is free."""
        from offsecgym.worldview.state import EventWorldState

        now = now or datetime.now(UTC)
        async with self.events.engine.connect() as connection:
            candidates = (
                (
                    await connection.execute(
                        select(worker_slots.c.worker_id).where(
                            worker_slots.c.run_id == run_id,
                            worker_slots.c.status.in_(("spawned", "started")),
                            worker_slots.c.lease_expires_at <= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
        recovered = 0
        for worker_id in candidates:
            async with self.worker_guard(run_id, worker_id, blocking=False) as acquired:
                if not acquired:
                    continue
                async with self.events.run_transaction(run_id) as tx:
                    row = (
                        (
                            await tx.connection.execute(
                                select(worker_slots).where(
                                    worker_slots.c.run_id == run_id,
                                    worker_slots.c.worker_id == worker_id,
                                )
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        row is None
                        or row["status"] not in ("spawned", "started")
                        or row["lease_expires_at"] is None
                        or row["lease_expires_at"] > now
                    ):
                        continue
                    task_id = row["task_id"]
                    history = await tx.read_run(run_id)
                    action_terminals = {
                        event.action_id
                        for event in history
                        if isinstance(event, (ActionCompleted, ActionFailed, ActionBlocked))
                    }
                    model_terminals = {
                        event.call_id
                        for event in history
                        if isinstance(event, (ModelCallCompleted, ModelCallFailed))
                    }
                    model_starts = {
                        event.call_id: event
                        for event in history
                        if isinstance(event, ModelCallStarted)
                    }
                    model_completions = {
                        event.call_id: event
                        for event in history
                        if isinstance(event, ModelCallCompleted)
                    }
                    action_rows = (
                        (
                            await tx.connection.execute(
                                select(action_reservations).where(
                                    action_reservations.c.run_id == run_id,
                                    action_reservations.c.worker_id == worker_id,
                                    action_reservations.c.task_id == task_id,
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    released_actions = 0
                    for action in action_rows:
                        action_id = action["action_id"]
                        if action_id not in action_terminals:
                            terminal = (
                                ActionFailed if action["released_at"] is None else ActionBlocked
                            )
                            await tx.append(
                                terminal(
                                    run_id=run_id,
                                    actor="controller",
                                    action_id=action_id,
                                    worker_id=worker_id,
                                    task_id=task_id,
                                    reason_code="worker_lease_expired",
                                )
                            )
                        if action["released_at"] is None:
                            await tx.connection.execute(
                                update(action_reservations)
                                .where(
                                    action_reservations.c.run_id == run_id,
                                    action_reservations.c.action_id == action_id,
                                )
                                .values(released_at=now)
                            )
                            await tx.append(
                                ActionReservationReleased(
                                    run_id=run_id,
                                    actor="controller",
                                    action_id=action_id,
                                    fingerprint=action["fingerprint"],
                                    worker_id=worker_id,
                                    task_id=task_id,
                                )
                            )
                            released_actions += 1
                    model_rows = (
                        (
                            await tx.connection.execute(
                                select(model_reservations).where(
                                    model_reservations.c.run_id == run_id,
                                    model_reservations.c.worker_id == worker_id,
                                    model_reservations.c.task_id == task_id,
                                    model_reservations.c.settled_at.is_(None),
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    usage = await self._usage(tx)
                    settled_calls = 0
                    recovered_tokens = 0
                    recovered_cost = 0
                    for model in model_rows:
                        call_id = model["call_id"]
                        completion = model_completions.get(call_id)
                        actual_input = completion.input_tokens if completion else 0
                        actual_output = completion.output_tokens if completion else 0
                        actual_tokens = actual_input + actual_output
                        actual_cost = (
                            cost_microusd(completion.estimated_cost_usd) if completion else 0
                        )
                        if call_id not in model_terminals:
                            started = model_starts.get(call_id)
                            await tx.append(
                                ModelCallFailed(
                                    run_id=run_id,
                                    actor="controller",
                                    call_id=call_id,
                                    worker_id=worker_id,
                                    task_id=task_id,
                                    reason_code="worker_lease_expired",
                                    causation_id=started.event_id if started else None,
                                )
                            )
                        await tx.connection.execute(
                            update(model_reservations)
                            .where(
                                model_reservations.c.run_id == run_id,
                                model_reservations.c.call_id == call_id,
                            )
                            .values(settled_at=now)
                        )
                        await tx.append(
                            ModelBudgetSettled(
                                schema_version=(
                                    "2" if model["reserved_input_tokens"] is not None else "1"
                                ),
                                run_id=run_id,
                                actor="controller",
                                call_id=call_id,
                                actual_tokens=actual_tokens,
                                actual_input_tokens=(
                                    actual_input
                                    if model["reserved_input_tokens"] is not None
                                    else None
                                ),
                                actual_output_tokens=(
                                    actual_output
                                    if model["reserved_input_tokens"] is not None
                                    else None
                                ),
                                reservation_error=(
                                    actual_tokens - model["reserved_tokens"]
                                    if model["reserved_input_tokens"] is not None
                                    else None
                                ),
                                input_reservation_error=(
                                    actual_input - model["reserved_input_tokens"]
                                    if model["reserved_input_tokens"] is not None
                                    else None
                                ),
                                actual_cost_microusd=actual_cost,
                                worker_id=worker_id,
                                task_id=task_id,
                            )
                        )
                        settled_calls += 1
                        recovered_tokens += actual_tokens
                        recovered_cost += actual_cost
                    _, coverage = EventWorldState._project(history)
                    released_coverage = 0
                    for claim in coverage.values():
                        if claim.task_id != task_id or claim.status != "active":
                            continue
                        await tx.append(
                            CoverageUpdated(
                                run_id=run_id,
                                actor="controller",
                                claim_id=claim.claim_id,
                                status="released",
                            )
                        )
                        await tx.append(
                            CoverageLeaseReleased(
                                run_id=run_id,
                                actor="controller",
                                claim_id=claim.claim_id,
                                task_id=task_id,
                                status="released",
                            )
                        )
                        released_coverage += 1
                    packet = next(
                        (
                            event
                            for event in history
                            if isinstance(event, WorkerPacketPrepared)
                            and event.worker_id == worker_id
                        ),
                        None,
                    )
                    outcome = any(
                        isinstance(
                            event, (WorkerObjectiveAction, WorkerBlocked, WorkerContractViolated)
                        )
                        and event.worker_id == worker_id
                        for event in history
                    )
                    if (
                        row["status"] == "started"
                        and packet is not None
                        and packet.packet.contract is not None
                        and not outcome
                    ):
                        await tx.append(
                            WorkerContractViolated(
                                run_id=run_id,
                                actor="controller",
                                worker_id=worker_id,
                                task_id=task_id,
                                reason_code="worker_lease_expired",
                            )
                        )
                    await tx.append(
                        WorkerLeaseRecovered(
                            run_id=run_id,
                            actor="controller",
                            worker_id=worker_id,
                            task_id=task_id,
                            expired_at=row["lease_expires_at"],
                            released_actions=released_actions,
                            settled_model_calls=settled_calls,
                            released_coverage=released_coverage,
                        )
                    )
                    if not any(
                        isinstance(event, WorkerDebriefed) and event.worker_id == worker_id
                        for event in history
                    ):
                        await tx.append(
                            WorkerDebriefed(
                                run_id=run_id,
                                actor="controller",
                                worker_id=worker_id,
                                task_id=task_id,
                                open_questions=("Worker lease expired before debrief",),
                            )
                        )
                    await tx.connection.execute(
                        update(run_usage)
                        .where(run_usage.c.run_id == run_id)
                        .values(
                            active_workers=usage["active_workers"]
                            - (1 if row["status"] == "started" else 0),
                            used_tokens=usage["used_tokens"] + recovered_tokens,
                            used_cost_microusd=usage["used_cost_microusd"] + recovered_cost,
                            reserved_tokens=usage["reserved_tokens"]
                            - sum(model["reserved_tokens"] for model in model_rows),
                            reserved_cost_microusd=usage["reserved_cost_microusd"]
                            - sum(model["reserved_cost_microusd"] for model in model_rows),
                        )
                    )
                    escrow = await self._worker_escrow(tx, worker_id, task_id)
                    if escrow is not None:
                        await tx.connection.execute(
                            update(worker_budget_accounts)
                            .where(
                                worker_budget_accounts.c.run_id == run_id,
                                worker_budget_accounts.c.worker_id == worker_id,
                            )
                            .values(
                                used_tokens=escrow["used_tokens"] + recovered_tokens,
                                reserved_tokens=escrow["reserved_tokens"]
                                - sum(model["reserved_tokens"] for model in model_rows),
                                used_cost_microusd=(escrow["used_cost_microusd"] + recovered_cost),
                                reserved_cost_microusd=escrow["reserved_cost_microusd"]
                                - sum(model["reserved_cost_microusd"] for model in model_rows),
                            )
                        )
                    await tx.connection.execute(
                        update(worker_slots)
                        .where(
                            worker_slots.c.run_id == run_id, worker_slots.c.worker_id == worker_id
                        )
                        .values(status="finished", lease_expires_at=None)
                    )
                    await tx.append(
                        WorkerFinished(
                            run_id=run_id,
                            actor="controller",
                            worker_id=worker_id,
                            task_id=task_id,
                            status="failed",
                        )
                    )
                    recovered += 1
        return recovered


class LocalActionReservations:
    """Single-process adapter for small unit tests; production uses PostgreSQL."""

    def __init__(self, events) -> None:
        self.events = events
        self.lock = asyncio.Lock()
        self.usage: dict[UUID, dict[str, object]] = {}
        self.action_ids: set[tuple[UUID, UUID]] = set()
        self.pending: dict[tuple[UUID, UUID], str] = {}
        self.active: dict[tuple[UUID, str], UUID] = {}

    async def reserve_request(
        self,
        action: ActionRequest,
        budget: Budget,
        requested_event: ActionRequested | None = None,
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
            self.action_ids.add((action.run_id, action.action_id))
            self.pending[(action.run_id, action.action_id)] = request_fingerprint(action)
            usage["used_actions"] += 1
            await self.events.append(
                ActionAttemptReserved(
                    run_id=action.run_id,
                    actor="controller",
                    action_id=action.action_id,
                    worker_id=action.worker_id,
                    task_id=action.task_id,
                )
            )
            if requested_event is not None:
                await self.events.append(requested_event)
            return None

    async def reserve_action(
        self,
        action: ActionRequest,
        budget: Budget,
        *,
        min_interval_seconds: float = 0,
        record_attempt: bool = True,
    ) -> str | None:
        if record_attempt:
            reason = await self.reserve_request(action, budget)
            if reason:
                return reason
        async with self.lock:
            usage = self.usage[action.run_id]
            fingerprint = request_fingerprint(action)
            if self.pending.get((action.run_id, action.action_id)) != fingerprint:
                return "action_not_pending"
            if (
                budget.max_http_requests is not None
                and usage["used_http_requests"] >= budget.max_http_requests
            ):
                return "http_budget_exhausted"
            now = datetime.now(UTC)
            last = usage["last_dispatch_at"]
            if last is not None and now - last < timedelta(seconds=min_interval_seconds):
                return "rate_limited"
            if (action.run_id, fingerprint) in self.active:
                return "action_already_reserved"
            del self.pending[(action.run_id, action.action_id)]
            self.active[(action.run_id, fingerprint)] = action.action_id
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
