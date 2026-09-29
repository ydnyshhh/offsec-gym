"""Gateway action contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from offsecgym.schemas.common import StrictModel, new_id


class ActionRequest(StrictModel):
    schema_version: Literal["1"] = "1"
    action_id: UUID = Field(default_factory=new_id)
    run_id: UUID
    worker_id: UUID | None = None
    identity_id: UUID | None = None
    kind: Literal["http_request"]
    destination: str = Field(min_length=1)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1)


class ActionResult(StrictModel):
    schema_version: Literal["1"] = "1"
    action_id: UUID
    status: Literal["completed", "blocked", "failed", "unknown"]
    reason_code: str | None = None
    evidence_id: UUID | None = None
    duration_ms: int = Field(ge=0)
