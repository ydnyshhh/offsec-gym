"""Typed append-only trace events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, field_validator, model_validator

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
    schema_version: Literal["1", "2"] = "2"
    type: Literal["range_started"] = "range_started"
    range_id: UUID | None = None
    build_id: UUID | None = None
    range_instance_id: UUID | None = None
    range_generation: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_versioned_identity(self) -> RangeStarted:
        if self.schema_version == "1":
            if self.range_id is None or any(
                item is not None
                for item in (self.build_id, self.range_instance_id, self.range_generation)
            ):
                raise ValueError("v1 range-start event requires only legacy range_id")
        elif self.range_id is not None or any(
            item is None for item in (self.build_id, self.range_instance_id, self.range_generation)
        ):
            raise ValueError("v2 range-start event requires build, instance, and generation")
        return self


class ActionRequested(TraceEvent):
    schema_version: Literal["1", "2"] = "2"
    type: Literal["action_requested"] = "action_requested"
    action_id: UUID
    action_type: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    method: str | None = None
    path_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    worker_id: UUID | None = None
    identity_id: UUID | None = None
    body_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    range_instance_id: UUID | None = None
    range_generation: int | None = Field(default=None, ge=0)
    request_artifact_id: UUID | None = None

    @model_validator(mode="after")
    def require_v2_provenance(self) -> ActionRequested:
        if self.schema_version == "2" and (
            self.range_instance_id is None
            or self.range_generation is None
            or self.request_artifact_id is None
        ):
            raise ValueError(
                "v2 action request requires instance, generation, and request artifact"
            )
        if self.schema_version == "1" and any(
            item is not None
            for item in (self.range_instance_id, self.range_generation, self.request_artifact_id)
        ):
            raise ValueError("v1 action request cannot include v2 provenance")
        return self


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
