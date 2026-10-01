"""Typed append-only trace events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, field_validator, model_validator

from offsecgym.schemas.common import StrictModel, new_id
from offsecgym.schemas.domain import CandidateFinding, CoverageClaim, ValidationResult, WorldFact
from offsecgym.schemas.specs import Budget


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
    task_id: UUID | None = None
    identity_id: UUID | None = None
    body_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    range_instance_id: UUID | None = None
    range_generation: int | None = Field(default=None, ge=0)
    request_artifact_id: UUID | None = None

    @model_validator(mode="after")
    def require_v2_provenance(self) -> ActionRequested:
        if self.worker_id is not None and self.task_id is None:
            raise ValueError("worker action event requires task_id")
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
    worker_id: UUID | None = None
    task_id: UUID | None = None
    reason_code: str = Field(min_length=1)


class ActionCompleted(TraceEvent):
    type: Literal["action_completed"] = "action_completed"
    action_id: UUID
    worker_id: UUID | None = None
    task_id: UUID | None = None
    evidence_id: UUID | None = None
    duration_ms: int = Field(ge=0)
    http_status: int | None = Field(default=None, ge=100, le=599)
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ActionFailed(TraceEvent):
    type: Literal["action_failed"] = "action_failed"
    action_id: UUID
    worker_id: UUID | None = None
    task_id: UUID | None = None
    reason_code: str = Field(min_length=1)


class BudgetUpdated(TraceEvent):
    type: Literal["budget_updated"] = "budget_updated"
    used_tokens: int = Field(ge=0)
    used_actions: int = Field(ge=0)


class ControllerBudgetDeclared(TraceEvent):
    type: Literal["controller_budget_declared"] = "controller_budget_declared"
    budget: Budget


class ModelCallStarted(TraceEvent):
    schema_version: Literal["1", "2"] = "2"
    type: Literal["model_call_started"] = "model_call_started"
    call_id: UUID
    worker_id: UUID | None = None
    task_id: UUID | None = None
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_artifact_id: UUID | None = None
    request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_request_artifact(self) -> ModelCallStarted:
        if self.worker_id is not None and self.task_id is None:
            raise ValueError("worker model call requires task_id")
        if self.schema_version == "2" and (
            self.request_artifact_id is None or self.request_sha256 != self.input_sha256
        ):
            raise ValueError("v2 model-call start requires matching request artifact and digest")
        return self


class ModelCallCompleted(TraceEvent):
    schema_version: Literal["1", "2"] = "2"
    type: Literal["model_call_completed"] = "model_call_completed"
    call_id: UUID
    worker_id: UUID | None = None
    task_id: UUID | None = None
    provider_response_id: str | None = None
    provider_status: str = Field(min_length=1)
    tool_call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    response_artifact_id: UUID | None = None
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_response_artifact(self) -> ModelCallCompleted:
        if self.schema_version == "2" and (
            self.response_artifact_id is None or self.response_sha256 is None
        ):
            raise ValueError("v2 model-call completion requires response artifact")
        return self


class ModelCallFailed(TraceEvent):
    type: Literal["model_call_failed"] = "model_call_failed"
    call_id: UUID
    worker_id: UUID | None = None
    task_id: UUID | None = None
    reason_code: str = Field(min_length=1)
    http_status: int | None = Field(default=None, ge=100, le=599)
    response_artifact_id: UUID | None = None
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_paired_response_reference(self) -> ModelCallFailed:
        if (self.response_artifact_id is None) != (self.response_sha256 is None):
            raise ValueError("failed model-call response artifact ID and digest must be paired")
        return self


class ModelToolRejected(TraceEvent):
    type: Literal["model_tool_rejected"] = "model_tool_rejected"
    model_call_id: UUID
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)


class WorldFactSubmitted(TraceEvent):
    type: Literal["world_fact_submitted"] = "world_fact_submitted"
    fact: WorldFact

    @model_validator(mode="after")
    def bind_fact_run(self) -> WorldFactSubmitted:
        if (
            self.run_id != self.fact.run_id
            or self.fact.schema_version not in {"3", "4"}
            or (
                self.fact.status != "hypothesized"
                and not (
                    self.actor == "controller"
                    and self.fact.schema_version == "4"
                    and self.fact.status == "observed"
                    and self.fact.kind in {"observation", "relationship"}
                    and len(self.fact.source_action_ids) == 1
                    and len(self.fact.evidence_ids) == 1
                )
            )
            or (
                self.fact.schema_version == "4"
                and (
                    self.fact.supersedes_fact_id is not None
                    or self.fact.superseded_by_fact_id is not None
                )
            )
        ):
            raise ValueError(
                "submitted world fact must be a same-run hypothesized claim "
                "or controller observation"
            )
        return self


class WorldFactStateChange(StrictModel):
    fact_id: UUID
    status: Literal[
        "hypothesized",
        "evidence_linked",
        "multi_evidence_linked",
        "observed",
        "corroborated",
        "validated",
        "contradicted",
        "superseded",
    ]
    reason_code: str = Field(min_length=1, max_length=128)
    contradicts_fact_ids: tuple[UUID, ...] = ()
    replacement_fact_id: UUID | None = None


class WorldFactAdjudicated(TraceEvent):
    type: Literal["world_fact_adjudicated"] = "world_fact_adjudicated"
    fact_id: UUID
    changes: tuple[WorldFactStateChange, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_changes(self) -> WorldFactAdjudicated:
        ids = [change.fact_id for change in self.changes]
        if self.fact_id not in ids or len(ids) != len(set(ids)):
            raise ValueError("world fact adjudication needs unique changes including target")
        return self


class CoverageClaimed(TraceEvent):
    type: Literal["coverage_claimed"] = "coverage_claimed"
    claim: CoverageClaim

    @model_validator(mode="after")
    def bind_claim_run(self) -> CoverageClaimed:
        if self.claim.run_id != self.run_id or self.claim.status != "active":
            raise ValueError("coverage claim must be active and bound to event run")
        return self


class CoverageUpdated(TraceEvent):
    type: Literal["coverage_updated"] = "coverage_updated"
    claim_id: UUID
    status: Literal["completed", "released"]


class CoverageLeaseAcquired(TraceEvent):
    type: Literal["coverage_lease_acquired"] = "coverage_lease_acquired"
    claim_id: UUID
    task_id: UUID
    component: str = Field(min_length=1)
    objective: str = Field(min_length=1)


class CoverageLeaseReleased(TraceEvent):
    type: Literal["coverage_lease_released"] = "coverage_lease_released"
    claim_id: UUID
    task_id: UUID
    status: Literal["completed", "released"]


class ActionReservationAcquired(TraceEvent):
    type: Literal["action_reservation_acquired"] = "action_reservation_acquired"
    action_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    worker_id: UUID | None = None
    task_id: UUID | None = None


class ActionAttemptReserved(TraceEvent):
    type: Literal["action_attempt_reserved"] = "action_attempt_reserved"
    action_id: UUID
    worker_id: UUID | None = None
    task_id: UUID | None = None


class ActionReservationReleased(TraceEvent):
    type: Literal["action_reservation_released"] = "action_reservation_released"
    action_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    worker_id: UUID | None = None
    task_id: UUID | None = None


class ModelBudgetReserved(TraceEvent):
    schema_version: Literal["1", "2"] = "2"
    type: Literal["model_budget_reserved"] = "model_budget_reserved"
    call_id: UUID
    reserved_tokens: int = Field(ge=0)
    reserved_input_tokens: int | None = Field(default=None, ge=0)
    reserved_output_tokens: int | None = Field(default=None, ge=0)
    request_bytes: int | None = Field(default=None, ge=0)
    reserved_cost_microusd: int = Field(ge=0)
    worker_id: UUID | None = None
    task_id: UUID | None = None

    @model_validator(mode="after")
    def validate_split(self) -> ModelBudgetReserved:
        if self.schema_version == "2" and (
            self.reserved_input_tokens is None
            or self.reserved_output_tokens is None
            or self.request_bytes is None
            or self.reserved_input_tokens + self.reserved_output_tokens != self.reserved_tokens
        ):
            raise ValueError(
                "v2 model reservation requires matching input/output split and request bytes"
            )
        return self


class ModelBudgetSettled(TraceEvent):
    schema_version: Literal["1", "2"] = "2"
    type: Literal["model_budget_settled"] = "model_budget_settled"
    call_id: UUID
    actual_tokens: int = Field(ge=0)
    actual_input_tokens: int | None = Field(default=None, ge=0)
    actual_output_tokens: int | None = Field(default=None, ge=0)
    reservation_error: int | None = None
    input_reservation_error: int | None = None
    actual_cost_microusd: int = Field(ge=0)
    worker_id: UUID | None = None
    task_id: UUID | None = None

    @model_validator(mode="after")
    def validate_split(self) -> ModelBudgetSettled:
        if self.schema_version == "2" and (
            self.actual_input_tokens is None
            or self.actual_output_tokens is None
            or self.reservation_error is None
            or self.input_reservation_error is None
            or self.actual_input_tokens + self.actual_output_tokens != self.actual_tokens
        ):
            raise ValueError(
                "v2 model settlement requires matching input/output split and reservation error"
            )
        return self


class WorkerSpawned(TraceEvent):
    type: Literal["worker_spawned"] = "worker_spawned"
    worker_id: UUID
    task_id: UUID
    objective: str = Field(min_length=1)


class WorkerStarted(TraceEvent):
    type: Literal["worker_started"] = "worker_started"
    worker_id: UUID
    task_id: UUID


class WorkerDebriefed(TraceEvent):
    type: Literal["worker_debriefed"] = "worker_debriefed"
    worker_id: UUID
    task_id: UUID
    new_fact_ids: tuple[UUID, ...] = ()
    candidate_finding_ids: tuple[UUID, ...] = ()
    coverage_claim_ids: tuple[UUID, ...] = ()
    open_questions: tuple[str, ...] = ()
    recommended_followups: tuple[str, ...] = ()


class WorkerFinished(TraceEvent):
    type: Literal["worker_finished"] = "worker_finished"
    worker_id: UUID
    task_id: UUID
    status: Literal["completed", "budget_exhausted", "failed", "cancelled"]


class ContextRetrieved(TraceEvent):
    type: Literal["context_retrieved"] = "context_retrieved"
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendered_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_fact_ids: tuple[UUID, ...] = ()
    max_facts: int = Field(ge=1, le=100)


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


class FindingSubmitted(TraceEvent):
    schema_version: Literal["2"] = "2"
    type: Literal["finding_submitted"] = "finding_submitted"
    finding: CandidateFinding

    @model_validator(mode="after")
    def bind_finding_run(self) -> FindingSubmitted:
        if self.run_id != self.finding.run_id:
            raise ValueError("finding submission run does not match event run")
        return self


class FindingValidated(TraceEvent):
    schema_version: Literal["2", "3"] = "3"
    type: Literal["finding_validated"] = "finding_validated"
    result: ValidationResult

    @model_validator(mode="after")
    def bind_validation_run(self) -> FindingValidated:
        if self.schema_version == "3" and (
            self.result.schema_version != "3" or self.result.run_id != self.run_id
        ):
            raise ValueError("validation result run does not match event run")
        return self


AnyTraceEvent = Annotated[
    RunStarted
    | RangeStarted
    | ActionRequested
    | ActionBlocked
    | ActionCompleted
    | ActionFailed
    | BudgetUpdated
    | ControllerBudgetDeclared
    | ModelCallStarted
    | ModelCallCompleted
    | ModelCallFailed
    | ModelToolRejected
    | WorldFactSubmitted
    | WorldFactAdjudicated
    | CoverageClaimed
    | CoverageUpdated
    | CoverageLeaseAcquired
    | CoverageLeaseReleased
    | ActionReservationAcquired
    | ActionAttemptReserved
    | ActionReservationReleased
    | ModelBudgetReserved
    | ModelBudgetSettled
    | WorkerSpawned
    | WorkerStarted
    | WorkerDebriefed
    | WorkerFinished
    | ContextRetrieved
    | FindingSubmitted
    | FindingValidated
    | RunCompleted,
    Field(discriminator="type"),
]
EVENT_ADAPTER: TypeAdapter[AnyTraceEvent] = TypeAdapter(AnyTraceEvent)


def parse_event(data: dict[str, object]) -> AnyTraceEvent:
    return EVENT_ADAPTER.validate_python(data)
