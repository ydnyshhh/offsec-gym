"""Transactional append and read of canonical trace events."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from offsecgym.schemas.events import AnyTraceEvent, parse_event
from offsecgym.storage.tables import events, runs


class PostgresEventStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def append(self, event: AnyTraceEvent) -> AnyTraceEvent:
        if event.sequence_number != 0:
            raise ValueError("new events must have sequence_number 0")
        async with self.engine.begin() as connection:
            await connection.execute(
                pg_insert(runs)
                .values(run_id=event.run_id, last_sequence=0, created_at=datetime.now(UTC))
                .on_conflict_do_nothing(index_elements=[runs.c.run_id])
            )
            run = (
                await connection.execute(
                    select(runs.c.last_sequence)
                    .where(runs.c.run_id == event.run_id)
                    .with_for_update()
                )
            ).one()
            existing = (
                await connection.execute(
                    select(events.c.payload).where(events.c.event_id == event.event_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                prior = parse_event(existing)
                if prior != event.model_copy(update={"sequence_number": prior.sequence_number}):
                    raise ValueError("event_id cannot be reused with different content")
                return prior
            persisted = event.model_copy(update={"sequence_number": run.last_sequence + 1})
            await connection.execute(
                insert(events).values(
                    event_id=persisted.event_id,
                    run_id=persisted.run_id,
                    sequence_number=persisted.sequence_number,
                    type=persisted.type,
                    occurred_at=persisted.occurred_at,
                    payload=persisted.model_dump(mode="json"),
                )
            )
            await connection.execute(
                update(runs)
                .where(runs.c.run_id == event.run_id)
                .values(last_sequence=persisted.sequence_number)
            )
            return persisted

    async def read_run(self, run_id: UUID) -> list[AnyTraceEvent]:
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(events.c.payload)
                    .where(events.c.run_id == run_id)
                    .order_by(events.c.sequence_number)
                )
            ).scalars()
            return [parse_event(payload) for payload in rows]
