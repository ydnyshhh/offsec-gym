"""Durable, isolated reporting events over a verified immutable source prefix."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update

from offsecgym.schemas.events import (
    AnyTraceEvent,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    ModelToolRejected,
    ProbeCheckpointSaved,
    ReporterEvidenceRetrieved,
    ReporterFindingSubmitted,
    ReporterFinished,
    ReporterStarted,
    ReportingBranchStarted,
    RunCompleted,
    RunStarted,
    TraceEvent,
    parse_event,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import reporting_branch_events, reporting_branches

_BRANCH_EVENT_TYPES = (
    ModelCallStarted,
    ModelCallCompleted,
    ModelCallFailed,
    ModelToolRejected,
    FindingSubmitted,
    FindingValidated,
    ReporterStarted,
    ReportingBranchStarted,
    ReporterFinished,
    ReporterEvidenceRetrieved,
    ReporterFindingSubmitted,
    RunCompleted,
)


def prefix_sha256(trace: list[TraceEvent]) -> str:
    """Use the same canonical trace digest as the frozen evidence packet."""
    raw = json.dumps(
        [item.model_dump(mode="json") for item in trace],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


class ReportingBranchStore:
    """EventStore view: one source prefix plus events private to one arm.

    The source run ID and gateway evidence IDs remain unchanged. Branch writes
    never enter the source `events` table, so findings and model turns cannot
    cross arms. A branch cannot dispatch HTTP or alter the source prefix.
    """

    def __init__(self, source: PostgresEventStore, branch_id: UUID, run_id: UUID) -> None:
        self.source = source
        self.branch_id = branch_id
        self.run_id = run_id

    @classmethod
    async def create(
        cls,
        source: PostgresEventStore,
        run_id: UUID,
        *,
        arm: Literal["fresh", "continuation"],
        checkpoint_id: UUID,
        branch_id: UUID | None = None,
    ) -> ReportingBranchStore:
        trace = list(await source.read_run(run_id))
        if (
            not trace
            or not isinstance(trace[0], RunStarted)
            or any(item.run_id != run_id for item in trace)
            or [item.sequence_number for item in trace] != list(range(1, len(trace) + 1))
            or any(
                isinstance(item, (FindingValidated, RunCompleted, ReporterStarted))
                for item in trace
            )
            or not isinstance(trace[-1], ProbeCheckpointSaved)
            or trace[-1].checkpoint_id != checkpoint_id
            or trace[-1].source_sequence != len(trace) - 1
            or trace[-1].source_trace_sha256 != prefix_sha256(trace[:-1])
        ):
            raise ValueError("reporting branch requires a complete unvalidated probe prefix")
        branch_id = branch_id or uuid4()
        async with source.engine.begin() as connection:
            await connection.execute(
                insert(reporting_branches).values(
                    branch_id=branch_id,
                    source_run_id=run_id,
                    arm=arm,
                    checkpoint_id=checkpoint_id,
                    source_sequence=len(trace),
                    source_sha256=prefix_sha256(trace),
                    last_sequence=len(trace),
                    created_at=datetime.now(UTC),
                )
            )
        return cls(source, branch_id, run_id)

    async def _prefix(self, sequence: int, digest: str) -> list[AnyTraceEvent]:
        source_trace = list(await self.source.read_run(self.run_id))
        prefix = source_trace[:sequence]
        if (
            len(prefix) != sequence
            or [item.sequence_number for item in prefix] != list(range(1, sequence + 1))
            or prefix_sha256(prefix) != digest
        ):
            raise ValueError("reporting source prefix changed or is incomplete")
        return prefix

    async def read_run(self, run_id: UUID) -> list[AnyTraceEvent]:
        if run_id != self.run_id:
            raise ValueError("reporting branch cannot read a different run")
        async with self.source.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(reporting_branches).where(
                            reporting_branches.c.branch_id == self.branch_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            if row["source_run_id"] != run_id:
                raise ValueError("reporting branch run binding changed")
            payloads = (
                (
                    await connection.execute(
                        select(reporting_branch_events.c.payload)
                        .where(reporting_branch_events.c.branch_id == self.branch_id)
                        .order_by(reporting_branch_events.c.sequence_number)
                    )
                )
                .scalars()
                .all()
            )
        prefix = await self._prefix(row["source_sequence"], row["source_sha256"])
        branch = [parse_event(payload) for payload in payloads]
        if [item.sequence_number for item in branch] != list(
            range(row["source_sequence"] + 1, row["last_sequence"] + 1)
        ):
            raise ValueError("reporting branch sequence is not contiguous")
        return [*prefix, *branch]

    async def append(self, event: AnyTraceEvent) -> AnyTraceEvent:
        if event.run_id != self.run_id or event.sequence_number != 0:
            raise ValueError("reporting event has a foreign run or preset sequence")
        if not isinstance(event, _BRANCH_EVENT_TYPES):
            raise ValueError("reporting branch cannot append gateway or probe events")
        async with self.source.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(reporting_branches)
                        .where(reporting_branches.c.branch_id == self.branch_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if row["source_run_id"] != self.run_id:
                raise ValueError("reporting branch run binding changed")
            if isinstance(event, ReportingBranchStarted):
                if (
                    row["last_sequence"] != row["source_sequence"]
                    or event.branch_id != self.branch_id
                    or event.arm != row["arm"]
                    or event.checkpoint_id != row["checkpoint_id"]
                    or event.source_trace_sha256 != row["source_sha256"]
                ):
                    raise ValueError("reporting branch start differs from stored binding")
            elif row["last_sequence"] == row["source_sequence"]:
                raise ValueError("reporting branch must start with its binding event")
            last = (
                await connection.execute(
                    select(reporting_branch_events.c.type).where(
                        reporting_branch_events.c.branch_id == self.branch_id,
                        reporting_branch_events.c.sequence_number == row["last_sequence"],
                    )
                )
            ).scalar_one_or_none()
            if last == "run_completed":
                raise ValueError("reporting branch is already closed")
            existing = (
                await connection.execute(
                    select(reporting_branch_events.c.payload).where(
                        reporting_branch_events.c.branch_id == self.branch_id,
                        reporting_branch_events.c.event_id == event.event_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                prior = parse_event(existing)
                if prior != event.model_copy(update={"sequence_number": prior.sequence_number}):
                    raise ValueError("branch event_id reused with different content")
                return prior
            sequence = row["last_sequence"] + 1
            persisted = event.model_copy(update={"sequence_number": sequence})
            await connection.execute(
                insert(reporting_branch_events).values(
                    branch_id=self.branch_id,
                    sequence_number=sequence,
                    event_id=persisted.event_id,
                    type=persisted.type,
                    occurred_at=persisted.occurred_at,
                    payload=persisted.model_dump(mode="json"),
                )
            )
            await connection.execute(
                update(reporting_branches)
                .where(reporting_branches.c.branch_id == self.branch_id)
                .values(last_sequence=sequence)
            )
            return persisted
