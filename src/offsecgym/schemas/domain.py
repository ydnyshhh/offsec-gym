"""Small domain contracts shared across subsystem interfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.specs import Budget


class AgentTask(StrictModel):
    task_id: UUID
    goal: str = Field(min_length=1)
    allowed_services: tuple[str, ...] = ()
    budget: Budget


class AgentContext(StrictModel):
    run_id: UUID
    objective: str = Field(min_length=1)
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
    range_id: UUID
    budget: Budget


class OrchestrationResult(StrictModel):
    run_id: UUID
    status: Literal["completed", "budget_exhausted", "failed", "cancelled"]
    worker_ids: tuple[UUID, ...] = ()


class EvidenceRef(StrictModel):
    evidence_id: UUID
    action_id: UUID
    description: str = Field(min_length=1)


class SecurityProperty(StrictModel):
    subject_role: str = Field(min_length=1)
    action: str = Field(min_length=1)
    object_relation: str = Field(min_length=1)
    expected: Literal["allow", "deny"]


class CandidateFinding(StrictModel):
    schema_version: Literal["1"] = "1"
    finding_id: UUID
    run_id: UUID
    claim: str = Field(min_length=1)
    family: str = Field(min_length=1)
    asset_id: UUID
    security_property: SecurityProperty
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    root_cause_id: UUID | None = None


class ValidationContext(StrictModel):
    run_id: UUID
    range_id: UUID
    ground_truth_ref: str = Field(min_length=1)


class ValidationResult(StrictModel):
    schema_version: Literal["1"] = "1"
    finding_id: UUID
    status: Literal["validated", "rejected", "inconclusive"]
    reason_codes: tuple[str, ...] = ()
    matched_property_id: UUID | None = None
    replay_evidence_ids: tuple[UUID, ...] = ()


class WorldFact(StrictModel):
    schema_version: Literal["1"] = "1"
    fact_id: UUID
    run_id: UUID
    subject_id: UUID
    predicate: str = Field(min_length=1)
    object_value: str = Field(min_length=1)
    evidence_ids: tuple[UUID, ...] = ()
    status: Literal[
        "hypothesized", "observed", "corroborated", "validated", "contradicted", "superseded"
    ] = "hypothesized"


class RangeStatus(StrictModel):
    range_id: UUID
    state: Literal["built", "starting", "healthy", "unhealthy", "stopped", "destroyed"]
    checked_at: datetime
