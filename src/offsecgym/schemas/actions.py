"""Gateway action contracts and bounded JSON payloads."""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from offsecgym.schemas.common import JsonValue, StrictModel, new_id

MAX_REQUEST_BODY_BYTES = 4096
MAX_JSON_DEPTH = 8
MAX_JSON_ELEMENTS = 256


def validate_json_body(value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
    """Reject non-JSON values and deeply nested or oversized objects before dispatch."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("JSON request body must be an object")
    elements = 0
    active_containers: set[int] = set()

    def visit(item: JsonValue, depth: int) -> None:
        nonlocal elements
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON request body exceeds maximum depth")
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in active_containers:
                raise ValueError("JSON request body contains a cycle")
            active_containers.add(identity)
            try:
                if isinstance(item, dict):
                    for key, child in item.items():
                        if not isinstance(key, str):
                            raise ValueError("JSON object keys must be strings")
                        elements += 1
                        if elements > MAX_JSON_ELEMENTS:
                            raise ValueError("JSON request body exceeds maximum elements")
                        visit(child, depth + 1)
                else:
                    for child in item:
                        elements += 1
                        if elements > MAX_JSON_ELEMENTS:
                            raise ValueError("JSON request body exceeds maximum elements")
                        visit(child, depth + 1)
            finally:
                active_containers.remove(identity)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("JSON request body contains a non-JSON value")

    visit(value, 0)
    try:
        encoded = json.dumps(value, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("JSON request body contains an invalid value") from exc
    if len(encoded) > MAX_REQUEST_BODY_BYTES:
        raise ValueError("JSON request body exceeds 4096 encoded bytes")
    return value


class ActionRequest(StrictModel):
    schema_version: Literal["1"] = "1"
    action_id: UUID = Field(default_factory=new_id)
    run_id: UUID
    worker_id: UUID | None = None
    task_id: UUID | None = None
    identity_id: UUID | None = None
    kind: Literal["http_request"]
    destination: str = Field(min_length=1)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=2048)
    json_body: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def bind_worker_task(self) -> ActionRequest:
        if self.worker_id is not None and self.task_id is None:
            raise ValueError("worker actions require task_id")
        return self

    @field_validator("json_body", mode="before")
    @classmethod
    def bound_json_body(cls, value: object) -> object:
        return validate_json_body(value)


class ActionResult(StrictModel):
    schema_version: Literal["1"] = "1"
    action_id: UUID
    status: Literal["completed", "blocked", "failed", "unknown"]
    reason_code: str | None = None
    evidence_id: UUID | None = None
    duration_ms: int = Field(ge=0)
    http_status: int | None = Field(default=None, ge=100, le=599)
    body_text: str | None = Field(default=None, max_length=16384)
    redirect_location: str | None = None
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    truncated: bool = False
