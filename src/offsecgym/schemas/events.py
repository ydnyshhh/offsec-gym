"""Typed append-only trace events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, field_validator

from offsecgym.schemas.common import StrictModel, new_id


class TraceEvent(StrictModel):
    schema_version: Literal["1"] = "1"
    event_id: UUID = Field(default_factory=new_id)
    run_id: UUID
    sequence_number: int = Field(default=0, ge=0)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str = Field(min_length=1)
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must include a timezone")
        return value


class RunStarted(TraceEvent):
    type: Literal["run_started"] = "run_started"
    experiment_hash: str = Field(min_length=1)


class RangeStarted(TraceEvent):
    type: Literal["range_started"] = "range_started"
    range_id: UUID


class ActionRequested(TraceEvent):
    type: Literal["action_requested"] = "action_requested"
    action_id: UUID
    action_type: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    method: str | None = None
    path_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ActionBlocked(TraceEvent):
    type: Literal["action_blocked"] = "action_blocked"
    action_id: UUID
    reason_code: str = Field(min_length=1)


class ActionCompleted(TraceEvent):
    type: Literal["action_completed"] = "action_completed"
    action_id: UUID
    evidence_id: UUID | None = None
    duration_ms: int = Field(ge=0)
    http_status: int | None = Field(default=None, ge=100, le=599)
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ActionFailed(TraceEvent):
    type: Literal["action_failed"] = "action_failed"
    action_id: UUID
    reason_code: str = Field(min_length=1)


class BudgetUpdated(TraceEvent):
    type: Literal["budget_updated"] = "budget_updated"
    used_tokens: int = Field(ge=0)
    used_actions: int = Field(ge=0)


RunStatus = Literal[
    "completed",
    "budget_exhausted",
    "agent_failed",
    "environment_failed",
    "provider_failed",
    "validation_failed",
    "cancelled",
]


class RunCompleted(TraceEvent):
    type: Literal["run_completed"] = "run_completed"
    status: RunStatus


AnyTraceEvent = Annotated[
    RunStarted
    | RangeStarted
    | ActionRequested
    | ActionBlocked
    | ActionCompleted
    | ActionFailed
    | BudgetUpdated
    | RunCompleted,
    Field(discriminator="type"),
]
EVENT_ADAPTER: TypeAdapter[AnyTraceEvent] = TypeAdapter(AnyTraceEvent)


def parse_event(data: dict[str, object]) -> AnyTraceEvent:
    return EVENT_ADAPTER.validate_python(data)
