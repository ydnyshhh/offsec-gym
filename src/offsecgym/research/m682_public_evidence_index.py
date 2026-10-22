"""Read-only, event-bound public observations and model-request delivery proofs."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from offsecgym.research.m661_checkpoint_analysis import _verify_binding
from offsecgym.research.m673_audit import CallEvidence, CellEvidence
from offsecgym.research.m682_object_resolution import PublicObject, objects_from_response
from offsecgym.runtime.manifests import StateStore
from offsecgym.schemas.events import ActionCompleted, ActionRequested, RangeStarted
from offsecgym.schemas.evidence import Evidence, RequestArtifact


@dataclass(frozen=True)
class VerifiedSource:
    run_id: UUID
    action_id: UUID
    request_artifact_id: UUID
    evidence_id: UUID
    range_instance_id: UUID
    range_generation: int
    identity_id: UUID | None
    source_phase: str
    originating_tool_call_id: str | None
    source_event_sequence: int
    method: str
    path: str
    http_status: int
    response_sha256: str
    body_text: str
    truncated: bool
    objects: tuple[PublicObject, ...]


@dataclass(frozen=True)
class Delivery:
    source_exists: bool
    source_verified: bool
    source_delivered: bool
    source_delivered_before_proposal: bool
    identifier_in_delivered_source: bool
    delivered_model_request_id: UUID | None


def _response_text(evidence: Evidence) -> str:
    return base64.b64decode(evidence.body_b64, validate=True).decode("utf-8", "replace")


def _read_source(
    requested: ActionRequested,
    completed: ActionCompleted,
    state_root: Path,
    family: str,
) -> VerifiedSource:
    """Abort on a mismatched action/request/evidence triple."""
    if (
        requested.request_artifact_id is None
        or requested.range_instance_id is None
        or requested.range_generation is None
        or completed.evidence_id is None
        or requested.run_id != completed.run_id
        or requested.action_id != completed.action_id
        or completed.causation_id != requested.event_id
        or completed.sequence_number <= requested.sequence_number
    ):
        raise ValueError("public source action event binding differs")
    directory = StateStore(state_root).instance_dir(requested.range_instance_id)
    request_path = directory / "requests" / f"{requested.request_artifact_id.hex}.json"
    evidence_path = directory / "evidence" / f"{completed.evidence_id.hex}.json"
    if request_path.is_symlink() or evidence_path.is_symlink():
        raise ValueError("unsafe public source artifact")
    request = RequestArtifact.model_validate_json(request_path.read_bytes())
    evidence = Evidence.model_validate_json(evidence_path.read_bytes())
    if (
        request.run_id != requested.run_id
        or request.action_id != requested.action_id
        or request.request_artifact_id != requested.request_artifact_id
        or request.range_instance_id != requested.range_instance_id
        or request.range_generation != requested.range_generation
        or request.source_phase != requested.source_phase
        or request.originating_call_id != requested.originating_call_id
        or request.originating_tool_call_id != requested.originating_tool_call_id
        or request.identity_id != requested.identity_id
        or request.destination != requested.destination
        or request.method != requested.method
        or hashlib.sha256(request.path.encode()).hexdigest() != requested.path_sha256
        or evidence.run_id != requested.run_id
        or evidence.action_id != requested.action_id
        or evidence.evidence_id != completed.evidence_id
        or evidence.request_artifact_id != request.request_artifact_id
        or evidence.range_instance_id != request.range_instance_id
        or evidence.range_generation != request.range_generation
        or evidence.identity_id != request.identity_id
        or evidence.http_status != completed.http_status
        or evidence.response_sha256 != completed.response_sha256
    ):
        raise ValueError("public source request/evidence provenance differs")
    body_text = _response_text(evidence)
    try:
        body = json.loads(body_text)
    except ValueError:
        body = None
    objects = objects_from_response(
        family=family,
        method=request.method,
        path=request.path,
        status=evidence.http_status,
        body=body,
        truncated=evidence.truncated,
    )
    return VerifiedSource(
        requested.run_id,
        requested.action_id,
        request.request_artifact_id,
        evidence.evidence_id,
        request.range_instance_id,
        request.range_generation,
        request.identity_id,
        requested.source_phase or "model",
        request.originating_tool_call_id,
        completed.sequence_number,
        request.method,
        request.path,
        evidence.http_status,
        evidence.response_sha256,
        body_text,
        evidence.truncated,
        objects,
    )


def build_sources(cell: CellEvidence, state_root: Path, family: str) -> tuple[VerifiedSource, ...]:
    """Index complete GET evidence for one pinned model run and its separate setup run."""
    if cell.setup_trace:
        receipt_path = state_root / "critical_checkpoints" / f"{cell.trace[0].run_id.hex}.json"
        if receipt_path.is_symlink():
            raise ValueError("unsafe checkpoint receipt")
        _verify_binding(receipt_path.read_bytes(), list(cell.trace), list(cell.setup_trace))
    sources: list[VerifiedSource] = []
    for trace in (cell.trace, cell.setup_trace):
        if not trace:
            continue
        ranges = [event for event in trace if isinstance(event, RangeStarted)]
        if len(ranges) != 1:
            raise ValueError("source range binding differs")
        range_event = ranges[0]
        done = {event.action_id: event for event in trace if isinstance(event, ActionCompleted)}
        for requested in trace:
            if not isinstance(requested, ActionRequested) or requested.method != "GET":
                continue
            completed = done.get(requested.action_id)
            if completed is None:
                continue
            if completed.evidence_id is None:
                raise ValueError("completed public GET has no evidence artifact")
            if (
                requested.range_instance_id != range_event.range_instance_id
                or requested.range_generation != range_event.range_generation
                or requested.destination != family
            ):
                raise ValueError("public source range identity differs")
            source = _read_source(requested, completed, state_root, family)
            if requested.source_phase is None:
                originating = next(
                    (
                        call
                        for call in cell.calls
                        if call.start.call_id == requested.originating_call_id
                    ),
                    None,
                )
                if (
                    originating is None
                    or originating.completed.sequence_number >= requested.sequence_number
                ):
                    raise ValueError("public source model-call origin differs")
                matched = False
                for item in originating.response.get("output", []):
                    if (
                        not isinstance(item, dict)
                        or item.get("type") != "function_call"
                        or item.get("name") != "http_request"
                        or item.get("call_id") != requested.originating_tool_call_id
                    ):
                        continue
                    try:
                        arguments = json.loads(item.get("arguments", ""))
                    except (TypeError, ValueError):
                        continue
                    matched = (
                        isinstance(arguments, dict)
                        and arguments.get("method") == source.method
                        and arguments.get("path") == source.path
                        and arguments.get("identity_id")
                        == (str(source.identity_id) if source.identity_id else None)
                    )
                    if matched:
                        break
                if not matched:
                    raise ValueError("public source tool-call origin differs")
            sources.append(source)
    return tuple(sorted(sources, key=lambda item: (str(item.run_id), item.source_event_sequence)))


def _input_text(request: dict[str, object]) -> str:
    """Serialize the actual provider request, preserving all delivered item forms."""
    return json.dumps(request.get("input", []), ensure_ascii=False, sort_keys=True)


def delivery_for(
    source: VerifiedSource,
    identifier: str,
    proposal_call: CallEvidence,
    prior_calls: tuple[CallEvidence, ...],
) -> Delivery:
    """Require a verified source ID in a linked model input before this response."""
    contains = any(obj.identifier == identifier for obj in source.objects)
    if not contains:
        return Delivery(True, True, False, False, False, None)
    candidates = [
        call
        for call in prior_calls
        if call.start.sequence_number <= proposal_call.start.sequence_number
    ]
    for call in candidates:
        if source.source_phase == "critical_checkpoint":
            # Separate-run setup evidence has no automatic delivery contract.
            continue
        if source.source_phase == "bootstrap":
            # Bootstrap facts enter the first model request through the verified
            # working-set builder, before any model-authored response can echo them.
            if call is not prior_calls[0] or identifier not in _input_text(call.request):
                continue
        else:
            if (
                source.run_id != call.start.run_id
                or source.source_event_sequence >= call.start.sequence_number
            ):
                continue
            delivered = False
            for item in call.request.get("input", []):
                if not isinstance(item, dict) or item.get("type") != "function_call_output":
                    continue
                if item.get("call_id") != source.originating_tool_call_id:
                    continue
                try:
                    payload = json.loads(item.get("output", ""))
                except (TypeError, ValueError):
                    continue
                if (
                    isinstance(payload, dict)
                    and payload.get("action_id") == str(source.action_id)
                    and payload.get("evidence_id") == str(source.evidence_id)
                    and payload.get("http_status") == source.http_status
                    and isinstance(payload.get("body_text"), str)
                    and source.body_text.startswith(payload["body_text"])
                    and identifier in payload["body_text"]
                ):
                    delivered = True
                    break
            if not delivered:
                continue
        return Delivery(True, True, True, True, True, call.start.request_artifact_id)
    return Delivery(True, True, False, False, False, None)
