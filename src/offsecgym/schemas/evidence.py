"""Restricted local artifacts for attempted requests and observed responses."""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from offsecgym.schemas.common import JsonValue, StrictModel


class RequestArtifact(StrictModel):
    schema_version: Literal["2"] = "2"
    request_artifact_id: UUID
    run_id: UUID
    action_id: UUID
    range_instance_id: UUID
    range_generation: int = Field(ge=0)
    worker_id: UUID | None = None
    task_id: UUID | None = None
    source_phase: Literal["bootstrap"] | None = None
    originating_call_id: UUID | None = None
    originating_tool_call_id: str | None = None
    identity_id: UUID | None = None
    destination: str = Field(min_length=1)
    method: str = Field(min_length=1)
    path: str = Field(min_length=1, max_length=2048)
    json_body: dict[str, JsonValue] | None = None


class Evidence(StrictModel):
    schema_version: Literal["2"] = "2"
    evidence_id: UUID
    run_id: UUID
    action_id: UUID
    range_instance_id: UUID
    range_generation: int = Field(ge=0)
    request_artifact_id: UUID
    worker_id: UUID | None = None
    task_id: UUID | None = None
    identity_id: UUID | None = None
    http_status: int = Field(ge=100, le=599)
    body_b64: str
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truncated: bool = False
    content_type: str | None = None
    redirect_location: str | None = None

    @model_validator(mode="after")
    def verify_response_digest(self) -> Evidence:
        try:
            raw = base64.b64decode(self.body_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("evidence body is not valid base64") from exc
        if len(raw) > 16384:
            raise ValueError("evidence response body exceeds 16384 bytes")
        if hashlib.sha256(raw).hexdigest() != self.response_sha256:
            raise ValueError("evidence response hash mismatch")
        return self
