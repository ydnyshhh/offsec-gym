"""Domain contracts shared by agents, validation, runtime, and worldview interfaces."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.specs import Budget


class AgentTask(StrictModel):
    task_id: UUID
    goal: str = Field(min_length=1)
    allowed_services: tuple[str, ...] = ()
    budget: Budget


class AgentVisibleRangeContext(StrictModel):
    """Explicitly projected, unprivileged context; no controller roster by default."""

    range_instance_id: UUID
    family: str = Field(min_length=1)
    visibility_policy: Literal["opaque_accounts", "known_roles", "white_box_accounts"] = (
        "opaque_accounts"
    )
    identity_ids: tuple[UUID, ...] = ()
    known_roles: tuple[str, ...] = ()

    @model_validator(mode="after")
    def enforce_visibility(self) -> AgentVisibleRangeContext:
        if self.visibility_policy == "opaque_accounts" and self.known_roles:
            raise ValueError("opaque accounts cannot reveal roles")
        return self


class AgentContext(StrictModel):
    run_id: UUID
    objective: str = Field(min_length=1)
    range: AgentVisibleRangeContext | None = None
    fact_ids: tuple[UUID, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    constraints: tuple[str, ...] = ()


class AgentResult(StrictModel):
    task_id: UUID
    status: Literal["completed", "budget_exhausted", "failed", "cancelled"]
    observation_ids: tuple[UUID, ...] = ()
    candidate_finding_ids: tuple[UUID, ...] = ()


class ExperimentContext(StrictModel):
    run_id: UUID
    range_instance_id: UUID
    range_generation: int = Field(ge=0)
    budget: Budget
    allowed_identity_ids: tuple[UUID, ...] = ()


class OrchestrationResult(StrictModel):
    run_id: UUID
    status: Literal["completed", "budget_exhausted", "failed", "cancelled"]
    worker_ids: tuple[UUID, ...] = ()


class EvidenceRef(StrictModel):
    evidence_id: UUID
    action_id: UUID
    description: str = Field(min_length=1)


class AuthorizationExpectation(StrictModel):
    kind: Literal["authorization"] = "authorization"
    subject_role: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    object_relation: str = Field(min_length=1)
    expected: Literal["allow", "deny"]


class FieldExposureExpectation(StrictModel):
    kind: Literal["field_exposure"] = "field_exposure"
    subject_role: str = Field(default="anonymous", min_length=1)
    action: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    forbidden_fields: tuple[str, ...] = Field(min_length=1)

    @field_validator("forbidden_fields")
    @classmethod
    def nonempty_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not field for field in value) or len(set(value)) != len(value):
            raise ValueError("forbidden fields must be distinct nonempty names")
        return value


class StateTransitionExpectation(StrictModel):
    kind: Literal["state_transition"] = "state_transition"
    subject_role: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    object_relation: str = Field(min_length=1)
    from_state: str = Field(min_length=1)
    to_state: str = Field(min_length=1)
    expected: Literal["allow", "deny"]
    allowed_roles: tuple[str, ...] = ()


SecurityExpectation = Annotated[
    AuthorizationExpectation | FieldExposureExpectation | StateTransitionExpectation,
    Field(discriminator="kind"),
]


class CandidateFinding(StrictModel):
    schema_version: Literal["2"] = "2"
    finding_id: UUID
    run_id: UUID
    range_instance_id: UUID
    range_generation: int = Field(ge=0)
    claim: str = Field(min_length=1)
    family: str = Field(min_length=1)
    asset_id: UUID
    security_property: SecurityExpectation
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    root_cause_hypothesis: str | None = None


class ValidationContext(StrictModel):
    run_id: UUID
    range_instance_id: UUID
    range_generation: int = Field(ge=0)
    ground_truth_ref: str = Field(min_length=1)


class ValidationResult(StrictModel):
    schema_version: Literal["2"] = "2"
    finding_id: UUID
    status: Literal["validated", "rejected", "inconclusive"]
    reason_codes: tuple[str, ...] = ()
    matched_property_id: UUID | None = None
    matched_root_cause_id: UUID | None = None
    replay_evidence_ids: tuple[UUID, ...] = ()


class EntityRef(StrictModel):
    entity_id: UUID
    entity_type: str = Field(min_length=1, max_length=128)


FactValue = str | int | float | bool | None | EntityRef


class WorldFact(StrictModel):
    schema_version: Literal["2"] = "2"
    fact_id: UUID
    run_id: UUID
    subject: EntityRef
    predicate: str = Field(min_length=1, max_length=128)
    object_value: FactValue
    source_worker_id: UUID | None = None
    source_event_ids: tuple[UUID, ...] = ()
    source_action_ids: tuple[UUID, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    status: Literal[
        "hypothesized", "observed", "corroborated", "validated", "contradicted", "superseded"
    ] = "hypothesized"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    supersedes_fact_id: UUID | None = None
    contradicts_fact_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_provenance(self) -> WorldFact:
        if not (
            self.source_worker_id
            or self.source_event_ids
            or self.source_action_ids
            or self.evidence_ids
        ):
            raise ValueError("world fact needs source provenance")
        if self.supersedes_fact_id == self.fact_id or self.fact_id in self.contradicts_fact_ids:
            raise ValueError("world fact cannot supersede or contradict itself")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("world fact timestamp must include timezone")
        if isinstance(self.object_value, str) and len(self.object_value) > 2048:
            raise ValueError("world fact value is too large")
        if type(self.object_value) is int and abs(self.object_value) > 10**18:
            raise ValueError("world fact integer is too large")
        if type(self.object_value) is float and (
            not math.isfinite(self.object_value) or abs(self.object_value) > 10**18
        ):
            raise ValueError("world fact float must be finite and bounded")
        return self


class RangeInstanceStatus(StrictModel):
    instance_id: UUID
    build_id: UUID
    generation: int = Field(ge=0)
    state: Literal["starting", "healthy", "unhealthy", "stopped", "destroyed"]
    checked_at: datetime


class RangeIdentity(StrictModel):
    identity_id: UUID
    username: str
    role: str
    workspace_id: UUID | None = None


class RangeControllerMetadata(StrictModel):
    instance_id: UUID
    build_id: UUID
    generation: int = Field(ge=0)
    project_name: str
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int
    image_id: str | None = None
    state: Literal["starting", "healthy", "unhealthy", "stopped", "destroyed"]
    family: str
    patched: bool
    pair_id: UUID | None = None
    identities: tuple[RangeIdentity, ...] = ()


def agent_visible_context(
    metadata: RangeControllerMetadata,
    *,
    visibility_policy: Literal["opaque_accounts", "known_roles", "white_box_accounts"] = (
        "opaque_accounts"
    ),
) -> AgentVisibleRangeContext:
    """Apply a visibility policy; never cast controller metadata into agent context."""
    return AgentVisibleRangeContext(
        range_instance_id=metadata.instance_id,
        family=metadata.family,
        visibility_policy=visibility_policy,
        identity_ids=(
            tuple(identity.identity_id for identity in metadata.identities)
            if visibility_policy == "white_box_accounts"
            else ()
        ),
        known_roles=(
            tuple(sorted({identity.role for identity in metadata.identities}))
            if visibility_policy != "opaque_accounts"
            else ()
        ),
    )
