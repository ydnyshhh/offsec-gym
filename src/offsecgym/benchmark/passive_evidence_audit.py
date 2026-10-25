"""Offline checks for retained public response provenance; no gateway calls."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import Field

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import ActionCompleted, ActionRequested
from offsecgym.schemas.evidence import Evidence, RequestArtifact

AUDIT_VERSION = "r0-passive-evidence-v1"


class CandidateReference(StrictModel):
    object_type: Literal["document", "support_ticket", "invoice"]
    source_field_path: str = Field(pattern=r"^\$\.[a-z][a-z0-9_]*$")
    observed_value: str = Field(min_length=1)
    action_id: UUID
    evidence_id: UUID


class RetainedPublicObservation(StrictModel):
    task_id: str = Field(min_length=1)
    visibility_contract_id: str = Field(min_length=1)
    request: RequestArtifact
    evidence: Evidence
    events: tuple[ActionRequested | ActionCompleted, ...]


class PassiveAuditResult(StrictModel):
    version: Literal["r0-passive-evidence-v1"] = AUDIT_VERSION
    record_is_well_formed: bool
    evidence_integrity_verified: bool
    field_provenance_valid: bool
    reason_codes: tuple[str, ...]
    q3_status: Literal["not_evaluated"] = "not_evaluated"


def _visible_route(method: str, path: str, routes: tuple[str, ...]) -> bool:
    for route in routes:
        verb, _, template = route.partition(" ")
        if verb != method or not template.startswith("/"):
            continue
        pattern = re.escape(template)
        pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/]+", pattern)
        if re.fullmatch(pattern, path):
            return True
    return False


def audit_retained_observation(
    record: RetainedPublicObservation,
    claim: CandidateReference,
    *,
    expected_task_id: str,
    expected_visibility_contract_id: str,
    public_routes: tuple[str, ...],
    visible_identity_ids: tuple[UUID, ...],
) -> PassiveAuditResult:
    """Check a claimed field against authoritative paired artifacts and events."""
    failures: list[str] = []
    request, evidence = record.request, record.evidence
    if record.task_id != expected_task_id:
        failures.append("task_binding")
    if record.visibility_contract_id != expected_visibility_contract_id:
        failures.append("visibility_contract")
    if request.method != "GET" or not _visible_route(request.method, request.path, public_routes):
        failures.append("public_route")
    if request.identity_id is not None and request.identity_id not in visible_identity_ids:
        failures.append("identity_visibility")
    if request.source_phase is not None:
        failures.append("reference_only_source")
    if claim.action_id != request.action_id or claim.evidence_id != evidence.evidence_id:
        failures.append("claim_binding")
    if (
        request.run_id != evidence.run_id
        or request.action_id != evidence.action_id
        or request.range_instance_id != evidence.range_instance_id
        or request.range_generation != evidence.range_generation
        or request.request_artifact_id != evidence.request_artifact_id
        or request.task_id != evidence.task_id
        or request.identity_id != evidence.identity_id
    ):
        failures.append("artifact_pairing")
    requested = [row for row in record.events if isinstance(row, ActionRequested)]
    completed = [row for row in record.events if isinstance(row, ActionCompleted)]
    if len(requested) != 1 or len(completed) != 1:
        failures.append("authoritative_event_count")
    else:
        start, end = requested[0], completed[0]
        if (
            start.schema_version != "2"
            or start.run_id != request.run_id
            or start.action_id != request.action_id
            or start.range_instance_id != request.range_instance_id
            or start.range_generation != request.range_generation
            or start.request_artifact_id != request.request_artifact_id
            or start.destination != request.destination
            or start.identity_id != request.identity_id
            or start.source_phase != request.source_phase
            or start.method != request.method
            or start.path_sha256 != hashlib.sha256(request.path.encode()).hexdigest()
            or end.run_id != evidence.run_id
            or end.action_id != evidence.action_id
            or end.evidence_id != evidence.evidence_id
            or end.response_sha256 != evidence.response_sha256
            or end.http_status != evidence.http_status
            or end.causation_id != start.event_id
        ):
            failures.append("event_artifact_binding")
        if start.sequence_number <= 0 or end.sequence_number <= start.sequence_number:
            failures.append("event_order")
    if evidence.truncated:
        failures.append("truncated_response")
    try:
        body = json.loads(__import__("base64").b64decode(evidence.body_b64, validate=True))
    except (ValueError, TypeError):
        body = None
    if not isinstance(body, dict):
        failures.append("unverified_response_object")
    else:
        field = claim.source_field_path[2:]
        if field.startswith("_") or field not in body or str(body[field]) != claim.observed_value:
            failures.append("source_field_value")
    valid = not failures
    return PassiveAuditResult(
        record_is_well_formed=valid,
        evidence_integrity_verified=valid,
        field_provenance_valid=valid,
        reason_codes=tuple(failures),
    )
