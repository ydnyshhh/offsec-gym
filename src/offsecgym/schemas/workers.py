"""Bounded handoff contracts for sequential ephemeral workers."""

from __future__ import annotations

import re
from typing import Literal
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
    source_phase: Literal["bootstrap"] | None = None


class CheckedAction(StrictModel):
    action_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: str = Field(min_length=1, max_length=8)
    path: str = Field(min_length=1, max_length=2048)
    identity_id: UUID | None = None
    source_worker_id: UUID | None = None
    source_phase: Literal["bootstrap"] | None = None
    body_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    body_json: str | None = Field(default=None, max_length=512)
    body_summary: str | None = Field(default=None, max_length=256)


class WorkerTaskContract(StrictModel):
    objective: str = Field(min_length=1, max_length=256)
    route_family: str = Field(min_length=1, max_length=128)
    target_entity_types: tuple[
        Literal["identity", "workspace", "document", "invoice", "ticket"], ...
    ]
    permitted_methods: tuple[Literal["GET", "POST"], ...]
    state_change_authorized: bool
    success_condition: Literal["objective_http_or_explicit_block"] = (
        "objective_http_or_explicit_block"
    )
    max_orientation_turns: int = Field(default=1, ge=0, le=2)

    @model_validator(mode="after")
    def valid_route(self) -> WorkerTaskContract:
        method, _, path = self.route_family.partition(" ")
        if method not in self.permitted_methods or not path.startswith("/api/"):
            raise ValueError("route family must use a permitted synthetic API method")
        if self.state_change_authorized != ("POST" in self.permitted_methods):
            raise ValueError("state change authorization must match permitted methods")
        return self

    def matches_objective_action(self, method: str, path: str) -> bool:
        expected_method, _, template = self.route_family.partition(" ")
        pattern = re.escape(template).replace(r"\{id\}", r"[0-9a-fA-F-]{36}")
        return method == expected_method and re.fullmatch(pattern, path) is not None


class WorkerTaskPacket(StrictModel):
    task_id: UUID
    worker_id: UUID
    objective: str = Field(min_length=1, max_length=256)
    contract: WorkerTaskContract | None = None
    budget_slice: Budget
    protected_future_tokens: int = Field(default=0, ge=0)
    protected_future_model_calls: int = Field(default=0, ge=0)
    protected_future_cost_microusd: int = Field(default=0, ge=0)
    protected_future_actions: int = Field(default=0, ge=0)
    protected_future_http_requests: int = Field(default=0, ge=0)
    relevant_entities: tuple[WorkerEntity, ...] = Field(default=(), max_length=12)
    relevant_evidence: tuple[WorkerEvidence, ...] = Field(default=(), max_length=16)
    prior_checked_actions: tuple[CheckedAction, ...] = Field(default=(), max_length=32)
    active_coverage: tuple[str, ...] = Field(default=(), max_length=8)
    completed_coverage: tuple[str, ...] = Field(default=(), max_length=12)
    known_hypotheses: tuple[str, ...] = Field(default=(), max_length=12)
    omitted_entity_details: int = Field(default=0, ge=0)
    omitted_entities: int = Field(default=0, ge=0)
    omitted_evidence: int = Field(default=0, ge=0)
    omitted_checked_actions: int = Field(default=0, ge=0)
    omitted_coverage: int = Field(default=0, ge=0)
    omitted_hypotheses: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def bounded_handoff(self) -> WorkerTaskPacket:
        if self.contract is not None and self.objective != self.contract.objective:
            raise ValueError("worker objective differs from task contract")
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
