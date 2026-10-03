"""Run-bound, oracle-free evidence handoff for the prospective M6.5 reporter."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.domain import CandidateFinding, FindingProposal
from offsecgym.schemas.events import (
    ActionBlocked,
    ActionCompleted,
    ActionFailed,
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    RangeStarted,
    ReporterStarted,
    RunCompleted,
    RunStarted,
    TraceEvent,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.solver.scripted import FindingSink

_UUID_IN_PATH = re.compile(
    r"(?<![0-9a-fA-F])([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})(?![0-9a-fA-F])"
)
MAX_PACKET_CHARS = 70_000
MAX_ACTIONS = 128
MAX_CANDIDATES = 100
EXCERPT_CHARS = 256


class WitnessAction(StrictModel):
    """Action index; the full response is available only through read-only lookup."""

    action_id: UUID
    evidence_id: UUID
    request_sequence: int = Field(gt=0)
    completion_sequence: int = Field(gt=0)
    method: str = Field(min_length=1, max_length=8)
    path: str = Field(min_length=1, max_length=2048)
    identity_id: UUID | None = None
    worker_id: UUID | None = None
    source_phase: str | None = None
    route_target_id: UUID | None = None
    response_object_id: UUID | None = None
    http_status: int = Field(ge=100, le=599)
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_json_body: dict[str, Any] | None = None
    response_excerpt: str = Field(max_length=EXCERPT_CHARS)
    truncated: bool

    @model_validator(mode="after")
    def ordered(self) -> WitnessAction:
        if self.request_sequence >= self.completion_sequence:
            raise ValueError("witness completion must follow its request")
        return self


class WitnessAttempt(StrictModel):
    """A requested action that ended without citable response evidence."""

    action_id: UUID
    request_sequence: int = Field(gt=0)
    terminal_sequence: int = Field(gt=0)
    method: str = Field(min_length=1, max_length=8)
    path: str = Field(min_length=1, max_length=2048)
    identity_id: UUID | None = None
    source_phase: str | None = None
    request_json_body: dict[str, Any] | None = None
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_status: Literal["blocked", "failed", "completed_without_evidence"]
    reason_code: str | None = None

    @model_validator(mode="after")
    def ordered(self) -> WitnessAttempt:
        if self.request_sequence >= self.terminal_sequence:
            raise ValueError("witness terminal must follow its request")
        return self


class ExistingCandidate(StrictModel):
    finding_id: UUID
    family: str
    asset_id: UUID
    claim: str = Field(max_length=1024)
    security_property: dict[str, Any]
    evidence_action_ids: tuple[UUID, ...]


class VisibleIdentity(StrictModel):
    identity_id: UUID
    role: str
    workspace_id: UUID | None = None
    username: str | None = None
    source_action_id: UUID


class ReporterPacket(StrictModel):
    schema_version: Literal["3"] = "3"
    run_id: UUID
    source_build_id: UUID
    source_experiment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    range_instance_id: UUID
    range_generation: int = Field(ge=0)
    actions: tuple[WitnessAction, ...] = Field(max_length=MAX_ACTIONS)
    unobserved_attempts: tuple[WitnessAttempt, ...] = Field(max_length=MAX_ACTIONS)
    identities: tuple[VisibleIdentity, ...] = ()
    existing_candidates: tuple[ExistingCandidate, ...] = Field(max_length=MAX_CANDIDATES)

    @model_validator(mode="after")
    def bounded_and_unique(self) -> ReporterPacket:
        action_ids = [action.action_id for action in self.actions] + [
            attempt.action_id for attempt in self.unobserved_attempts
        ]
        evidence_ids = [action.evidence_id for action in self.actions]
        sequences = [action.completion_sequence for action in self.actions]
        attempt_sequences = [attempt.request_sequence for attempt in self.unobserved_attempts]
        candidate_ids = [item.finding_id for item in self.existing_candidates]
        if (
            len(action_ids) != len(set(action_ids))
            or len(evidence_ids) != len(set(evidence_ids))
            or sequences != sorted(sequences)
            or attempt_sequences != sorted(attempt_sequences)
            or len(candidate_ids) != len(set(candidate_ids))
        ):
            raise ValueError("reporter packet has duplicate or unordered evidence")
        if len(self.model_dump_json()) > MAX_PACKET_CHARS:
            raise ValueError("reporter packet exceeds its declared context bound")
        return self

    @property
    def bundle_sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


@dataclass(frozen=True)
class TrustedAction:
    request: RequestArtifact
    evidence: Evidence


@dataclass(frozen=True)
class ReporterEvidenceBundle:
    packet: ReporterPacket
    by_action: dict[UUID, TrustedAction]

    def get_evidence(self, evidence_id: UUID) -> dict[str, Any]:
        action = next(
            (entry for entry in self.packet.actions if entry.evidence_id == evidence_id), None
        )
        if action is None:
            raise ValueError("evidence is outside the reporter evidence packet")
        return self.get_action(action.action_id)

    def get_entity(self, entity_id: UUID) -> dict[str, Any]:
        identities = [
            x.model_dump(mode="json") for x in self.packet.identities if x.identity_id == entity_id
        ]
        actions = [
            x.model_dump(mode="json")
            for x in self.packet.actions
            if x.route_target_id == entity_id or x.response_object_id == entity_id
        ]
        if not identities and not actions:
            raise ValueError("entity is outside the reporter evidence packet")
        return {"entity_id": str(entity_id), "identities": identities, "actions": actions[:32]}

    def search_evidence(self, query: str) -> dict[str, Any]:
        if not query or len(query) > 128:
            raise ValueError("evidence query must contain 1 to 128 characters")
        needle = query.casefold()
        hits = [
            {
                "action_id": str(x.action_id),
                "evidence_id": str(x.evidence_id),
                "sequence": x.completion_sequence,
                "method": x.method,
                "path": x.path,
                "http_status": x.http_status,
            }
            for x in self.packet.actions
            if needle in x.path.casefold() or needle in x.response_excerpt.casefold()
        ]
        return {"query": query, "matches": hits[:24], "total_matches": len(hits)}

    def get_action(self, action_id: UUID) -> dict[str, Any]:
        """Read a completed artifact; no live range or oracle access exists here."""
        trusted = self.by_action.get(action_id)
        if trusted is None:
            raise ValueError("action is outside the reporter evidence packet")
        raw = base64.b64decode(trusted.evidence.body_b64, validate=True)
        return {
            "action_id": str(action_id),
            "evidence_id": str(trusted.evidence.evidence_id),
            "method": trusted.request.method,
            "path": trusted.request.path,
            "identity_id": (
                str(trusted.request.identity_id) if trusted.request.identity_id else None
            ),
            "request_json_body": trusted.request.json_body,
            "http_status": trusted.evidence.http_status,
            "response_body": raw.decode("utf-8", errors="replace"),
            "response_sha256": trusted.evidence.response_sha256,
            "truncated": trusted.evidence.truncated,
        }

    def check_proposal(self, proposal: FindingProposal) -> None:
        for ref in proposal.evidence:
            trusted = self.by_action.get(ref.action_id)
            if trusted is None or trusted.evidence.evidence_id != ref.evidence_id:
                raise ValueError("reporter finding cites evidence outside its packet")


class ReadOnlyReporterTools:
    """The reporter can retrieve trusted evidence and submit claims, never dispatch HTTP."""

    def __init__(self, bundle: ReporterEvidenceBundle, findings: FindingSink) -> None:
        self.bundle = bundle
        self.findings = findings

    def get_action_evidence(self, action_id: UUID) -> dict[str, Any]:
        return self.bundle.get_action(action_id)

    def get_evidence(self, evidence_id: UUID) -> dict[str, Any]:
        return self.bundle.get_evidence(evidence_id)

    def get_entity(self, entity_id: UUID) -> dict[str, Any]:
        return self.bundle.get_entity(entity_id)

    def search_evidence(self, query: str) -> dict[str, Any]:
        return self.bundle.search_evidence(query)

    async def submit_finding(self, proposal: FindingProposal) -> CandidateFinding:
        self.bundle.check_proposal(proposal)
        return await self.findings.submit(proposal)


def _body_sha(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _request_fingerprint(request: RequestArtifact) -> str:
    raw = json.dumps(
        [request.method, request.path, str(request.identity_id), request.json_body],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _target_from_path(path: str) -> UUID | None:
    match = _UUID_IN_PATH.search(path)
    return UUID(match.group(1)) if match else None


def _response_object_id(raw: bytes) -> UUID | None:
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("id"), str):
            return UUID(parsed["id"])
    except (UnicodeDecodeError, ValueError, TypeError):
        pass
    return None


def build_reporter_bundle(
    trace: list[TraceEvent], state_dir: Path, *, expected_run_id: UUID
) -> ReporterEvidenceBundle:
    """Verify event/artifact closure before exposing any evidence to a reporter."""
    started = [item for item in trace if isinstance(item, RunStarted)]
    ranges = [item for item in trace if isinstance(item, RangeStarted)]
    if (
        len(started) != 1
        or len(ranges) != 1
        or started[0].run_id != expected_run_id
        or ranges[0].run_id != expected_run_id
        or ranges[0].range_instance_id is None
        or ranges[0].range_generation is None
        or ranges[0].build_id is None
    ):
        raise ValueError("reporter source requires one versioned run and range")
    if any(item.run_id != expected_run_id for item in trace):
        raise ValueError("reporter trace mixes run IDs")
    if any(isinstance(item, (FindingValidated, ReporterStarted, RunCompleted)) for item in trace):
        raise ValueError("reporter source must freeze before validation and run completion")
    trace_raw = json.dumps(
        [item.model_dump(mode="json") for item in trace],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    sequence = [item.sequence_number for item in trace]
    if sequence != sorted(sequence) or len(sequence) != len(set(sequence)) or min(sequence) <= 0:
        raise ValueError("reporter trace sequence is not authoritative and ordered")
    instance_id = ranges[0].range_instance_id
    generation = ranges[0].range_generation
    requests = [item for item in trace if isinstance(item, ActionRequested)]
    requested = {item.action_id: item for item in requests}
    if len(requested) != len(requests):
        raise ValueError("reporter trace repeats an action request")
    completions = [item for item in trace if isinstance(item, ActionCompleted)]
    completed_ids = [item.action_id for item in completions]
    if len(completed_ids) != len(set(completed_ids)):
        raise ValueError("reporter trace repeats an action completion")
    terminals = [
        item for item in trace if isinstance(item, (ActionCompleted, ActionBlocked, ActionFailed))
    ]
    terminal_by_id = {item.action_id: item for item in terminals if item.action_id in requested}
    if len(terminal_by_id) != len(requested) or sum(
        item.action_id in requested for item in terminals
    ) != len(requested):
        raise ValueError("reporter source request lacks one terminal event")
    indexed: list[WitnessAction] = []
    unobserved: list[WitnessAttempt] = []
    visible_identities: dict[UUID, VisibleIdentity] = {}
    by_action: dict[UUID, TrustedAction] = {}
    instance_dir = state_dir / "instances" / instance_id.hex
    verified_requests: dict[UUID, RequestArtifact] = {}
    for event in requests:
        terminal = terminal_by_id[event.action_id]
        if (
            event.schema_version != "2"
            or event.range_instance_id != instance_id
            or event.range_generation != generation
            or event.request_artifact_id is None
            or event.method is None
            or event.path_sha256 is None
            or event.sequence_number >= terminal.sequence_number
            or event.worker_id != terminal.worker_id
            or event.task_id != terminal.task_id
        ):
            raise ValueError("requested action lacks same-generation terminal provenance")
        request_path = instance_dir / "requests" / f"{event.request_artifact_id.hex}.json"
        request = RequestArtifact.model_validate_json(request_path.read_bytes())
        if (
            request.run_id != expected_run_id
            or request.range_instance_id != instance_id
            or request.range_generation != generation
            or request.action_id != event.action_id
            or request.request_artifact_id != event.request_artifact_id
            or request.method != event.method
            or request.destination != event.destination
            or request.identity_id != event.identity_id
            or request.worker_id != event.worker_id
            or request.task_id != event.task_id
            or request.source_phase != event.source_phase
            or hashlib.sha256(request.path.encode()).hexdigest() != event.path_sha256
            or _body_sha(request.json_body) != event.body_sha256
        ):
            raise ValueError("reporter request event and trusted artifact disagree")
        verified_requests[event.action_id] = request
        if isinstance(terminal, ActionCompleted) and terminal.evidence_id is not None:
            continue
        unobserved.append(
            WitnessAttempt(
                action_id=event.action_id,
                request_sequence=event.sequence_number,
                terminal_sequence=terminal.sequence_number,
                method=request.method,
                path=request.path,
                identity_id=request.identity_id,
                source_phase=request.source_phase,
                request_json_body=request.json_body,
                request_fingerprint=_request_fingerprint(request),
                terminal_status=(
                    "blocked"
                    if isinstance(terminal, ActionBlocked)
                    else "failed"
                    if isinstance(terminal, ActionFailed)
                    else "completed_without_evidence"
                ),
                reason_code=(
                    terminal.reason_code
                    if isinstance(terminal, (ActionBlocked, ActionFailed))
                    else None
                ),
            )
        )
    for done in completions:
        if done.evidence_id is None:
            continue
        event = requested.get(done.action_id)
        if event is None or done.http_status is None or done.response_sha256 is None:
            raise ValueError("completed action lacks same-generation request provenance")
        evidence_path = instance_dir / "evidence" / f"{done.evidence_id.hex}.json"
        request = verified_requests[done.action_id]
        evidence = Evidence.model_validate_json(evidence_path.read_bytes())
        if (
            evidence.run_id != expected_run_id
            or evidence.range_instance_id != instance_id
            or evidence.range_generation != generation
            or evidence.action_id != done.action_id
            or evidence.request_artifact_id != request.request_artifact_id
            or evidence.evidence_id != done.evidence_id
            or evidence.worker_id != done.worker_id
            or evidence.task_id != done.task_id
            or evidence.http_status != done.http_status
            or evidence.response_sha256 != done.response_sha256
        ):
            raise ValueError("reporter action event and trusted artifacts disagree")
        raw = base64.b64decode(evidence.body_b64, validate=True)
        if request.path == "/api/me" and request.identity_id and evidence.http_status == 200:
            try:
                me = json.loads(raw)
                if isinstance(me, dict) and me.get("id") == str(request.identity_id):
                    visible_identities[request.identity_id] = VisibleIdentity(
                        identity_id=request.identity_id,
                        role=me["role"],
                        workspace_id=UUID(me["workspace_id"]) if me.get("workspace_id") else None,
                        username=me.get("username"),
                        source_action_id=done.action_id,
                    )
            except (ValueError, KeyError, TypeError):
                raise ValueError("trusted identity response is malformed") from None
        indexed.append(
            WitnessAction(
                action_id=done.action_id,
                evidence_id=done.evidence_id,
                request_sequence=event.sequence_number,
                completion_sequence=done.sequence_number,
                method=request.method,
                path=request.path,
                identity_id=request.identity_id,
                worker_id=request.worker_id,
                source_phase=request.source_phase,
                route_target_id=_target_from_path(request.path),
                response_object_id=_response_object_id(raw),
                http_status=evidence.http_status,
                response_sha256=evidence.response_sha256,
                request_fingerprint=_request_fingerprint(request),
                request_json_body=request.json_body,
                response_excerpt=raw.decode("utf-8", errors="replace")[:EXCERPT_CHARS],
                truncated=evidence.truncated,
            )
        )
        by_action[done.action_id] = TrustedAction(request, evidence)
    indexed.sort(key=lambda item: item.completion_sequence)
    unobserved.sort(key=lambda item: item.request_sequence)
    submissions = [item for item in trace if isinstance(item, FindingSubmitted)]
    candidates: list[ExistingCandidate] = []
    for submitted in submissions:
        finding: CandidateFinding = submitted.finding
        if finding.run_id != expected_run_id:
            raise ValueError("reporter source finding belongs to another run")
        candidates.append(
            ExistingCandidate(
                finding_id=finding.finding_id,
                family=finding.family,
                asset_id=finding.asset_id,
                claim=finding.claim[:1024],
                security_property=finding.security_property.model_dump(mode="json"),
                evidence_action_ids=tuple(ref.action_id for ref in finding.evidence),
            )
        )
    packet = ReporterPacket(
        run_id=expected_run_id,
        source_build_id=ranges[0].build_id,
        source_experiment_sha256=started[0].experiment_hash,
        source_trace_sha256=hashlib.sha256(trace_raw).hexdigest(),
        range_instance_id=instance_id,
        range_generation=generation,
        actions=tuple(indexed),
        unobserved_attempts=tuple(unobserved),
        identities=tuple(visible_identities[key] for key in sorted(visible_identities)),
        existing_candidates=tuple(candidates),
    )
    return ReporterEvidenceBundle(packet, by_action)
