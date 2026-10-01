"""Deterministic M6.3 task budget requests and lease accounting."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from offsecgym.schemas.common import StrictModel

TaskState = Literal["PENDING", "READY", "RUNNING", "BLOCKED", "COMPLETED", "FAILED"]


class TaskBudgetRequest(StrictModel):
    minimum_viable_tokens: int = Field(gt=0)
    preferred_tokens: int = Field(gt=0)
    max_tokens: int = Field(gt=0)
    minimum_model_calls: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    expected_actions: int = Field(gt=0)
    max_actions: int = Field(gt=0)
    expected_http_requests: int = Field(gt=0)
    max_http_requests: int = Field(gt=0)

    @model_validator(mode="after")
    def monotonic(self) -> TaskBudgetRequest:
        if not (
            self.minimum_viable_tokens <= self.preferred_tokens <= self.max_tokens
            and self.minimum_model_calls <= self.max_model_calls
            and self.expected_actions <= self.max_actions
            and self.expected_http_requests <= self.max_http_requests
        ):
            raise ValueError("task budget minimum, preferred, and maximum must be ordered")
        return self


class AdmissionTask(StrictModel):
    worker_id: UUID
    task_id: UUID
    kind: str = Field(min_length=1)
    phase: Literal["ready", "forecast"]
    request: TaskBudgetRequest
