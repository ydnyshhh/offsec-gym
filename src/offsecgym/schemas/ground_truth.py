"""Private, versioned evaluator ground truth. Never pass these models to an agent."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.domain import SecurityExpectation


class RootCause(StrictModel):
    root_cause_id: UUID
    slug: str = Field(min_length=1)
    description: str = Field(min_length=1)

    @field_validator("root_cause_id")
    @classmethod
    def deterministic_id(cls, value: UUID) -> UUID:
        if value.version != 5:
            raise ValueError("root-cause ID must be deterministic UUIDv5")
        return value


class GroundTruthSubject(StrictModel):
    identity_id: UUID | None = None
    role: str = Field(min_length=1)
    workspace_id: UUID | None = None


class GroundTruthObject(StrictModel):
    object_id: UUID
    resource_type: str = Field(min_length=1)
    workspace_id: UUID | None = None


ProofKind = Literal[
    "identity",
    "foreign_document_id",
    "foreign_invoice_id",
    "foreign_ticket_id",
    "response_body",
    "refund_response",
    "invoice_status",
    "anonymous_request",
    "billing_email_in_response",
]


class ProofRequirement(StrictModel):
    kind: ProofKind


class GroundTruthProperty(StrictModel):
    property_id: UUID
    slug: str = Field(min_length=1)
    family: str = Field(min_length=1)
    component: str = Field(min_length=1)
    subject: GroundTruthSubject
    object: GroundTruthObject
    expectation: SecurityExpectation
    vulnerable_effect: str = Field(min_length=1)
    root_cause_id: UUID
    proof_requirements: tuple[ProofRequirement, ...] = Field(min_length=1)
    active: bool

    @field_validator("property_id")
    @classmethod
    def deterministic_id(cls, value: UUID) -> UUID:
        if value.version != 5:
            raise ValueError("property ID must be deterministic UUIDv5")
        return value

    @model_validator(mode="after")
    def check_subject_and_expectation(self) -> GroundTruthProperty:
        if self.subject.role != self.expectation.subject_role:
            raise ValueError("oracle subject role and expectation disagree")
        if self.object.resource_type != self.expectation.resource_type:
            raise ValueError("oracle object type and expectation disagree")
        if self.subject.role == "anonymous" and self.subject.identity_id is not None:
            raise ValueError("anonymous subject cannot have an identity")
        return self


class GroundTruthManifest(StrictModel):
    schema_version: Literal["2"] = "2"
    scenario_id: str = Field(min_length=1)
    build_id: UUID
    pair_id: UUID
    seed: int = Field(ge=0)
    variant: Literal["vulnerable", "patched", "selective"]
    root_causes: tuple[RootCause, ...] = Field(min_length=1)
    properties: tuple[GroundTruthProperty, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_closure(self) -> GroundTruthManifest:
        cause_ids = {cause.root_cause_id for cause in self.root_causes}
        property_ids = {prop.property_id for prop in self.properties}
        if len(cause_ids) != len(self.root_causes):
            raise ValueError("duplicate root-cause ID")
        if len(property_ids) != len(self.properties):
            raise ValueError("duplicate property ID")
        if len({cause.slug for cause in self.root_causes}) != len(self.root_causes):
            raise ValueError("duplicate root-cause slug")
        if len({prop.slug for prop in self.properties}) != len(self.properties):
            raise ValueError("duplicate property slug")
        if any(prop.root_cause_id not in cause_ids for prop in self.properties):
            raise ValueError("property references unknown root cause")
        if self.variant == "vulnerable" and any(not prop.active for prop in self.properties):
            raise ValueError("vulnerable variant contains inactive property")
        if self.variant == "patched" and any(prop.active for prop in self.properties):
            raise ValueError("patched variant contains active property")
        return self

    @property
    def active_property_ids(self) -> tuple[UUID, ...]:
        return tuple(prop.property_id for prop in self.properties if prop.active)
