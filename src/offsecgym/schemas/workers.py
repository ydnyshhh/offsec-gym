"""Bounded handoff contracts for sequential ephemeral workers."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.specs import Budget


class WorkerEntity(StrictModel):
    entity_id: UUID
    entity_type: str = Field(min_length=1, max_length=32)
    details: tuple[str, ...] = Field(default=(), max_length=6)


class WorkerEvidence(StrictModel):
    entity_id: UUID
    action_id: UUID
    evidence_id: UUID
    source_worker_id: UUID | None = None


class CheckedAction(StrictModel):
    action_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: str = Field(min_length=1, max_length=8)
    path: str = Field(min_length=1, max_length=2048)
    identity_id: UUID | None = None


class WorkerTaskPacket(StrictModel):
    task_id: UUID
    worker_id: UUID
    objective: str = Field(min_length=1, max_length=256)
    budget_slice: Budget
    relevant_entities: tuple[WorkerEntity, ...] = Field(default=(), max_length=12)
    relevant_evidence: tuple[WorkerEvidence, ...] = Field(default=(), max_length=16)
    prior_checked_actions: tuple[CheckedAction, ...] = Field(default=(), max_length=32)
    active_coverage: tuple[str, ...] = Field(default=(), max_length=8)
    completed_coverage: tuple[str, ...] = Field(default=(), max_length=12)
    known_hypotheses: tuple[str, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def bounded_handoff(self) -> WorkerTaskPacket:
        if len(self.model_dump_json()) > 10000:
            raise ValueError("worker packet exceeds 10000 characters")
        return self


class WorkerDebrief(StrictModel):
    task_id: UUID
    worker_id: UUID
    new_fact_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    candidate_finding_ids: tuple[UUID, ...] = Field(default=(), max_length=30)
    completed_coverage_ids: tuple[UUID, ...] = Field(default=(), max_length=30)
    open_questions: tuple[str, ...] = Field(default=(), max_length=12)
    recommended_followups: tuple[str, ...] = Field(default=(), max_length=12)
