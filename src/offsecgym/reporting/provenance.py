"""Current-run public evidence catalog and strictly clerical finding preflight."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import Field, ValidationError

from offsecgym.interfaces import EventStore
from offsecgym.runtime.manifests import StateStore
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import ActionCompleted, ActionRequested, RunCompleted
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.solver.range_surface import range_surface


def canonical_route(family: str, method: str, path: str) -> str | None:
    """Match public route templates, substituting UUID path segments only."""
    if not path.startswith("/") or "?" in path or "#" in path or "//" in path:
        return None
    actual = path.split("/")
    matches: list[str] = []
    for route in range_surface(family).routes:
        verb, template = route.split(" ", 1)
        parts = template.split("/")
        if verb != method or len(parts) != len(actual):
            continue
        for expected, observed in zip(parts, actual, strict=True):
            if expected.startswith("{") and expected.endswith("}"):
                try:
                    UUID(observed)
                except ValueError:
                    break
            elif expected != observed:
                break
        else:
            matches.append(route)
    return matches[0] if len(matches) == 1 else None


class ListCandidateEvidenceArgs(StrictModel):
    identity_id: UUID | None
    method: str | None
    route_shape: str | None
    sequence_start: int | None
    sequence_end: int | None
    status_code: int | None


class PreflightFindingArgs(StrictModel):
    finding_tool: str = Field(min_length=1, max_length=64)
    arguments_json: str = Field(min_length=2, max_length=16000)


REPORTING_TOOL_MODELS: dict[str, type[StrictModel]] = {
    "list_candidate_evidence": ListCandidateEvidenceArgs,
    "preflight_finding": PreflightFindingArgs,
}


@dataclass(frozen=True)
class CatalogEntry:
    action_id: UUID
    evidence_id: UUID
    sequence: int
    identity_id: UUID | None
    method: str
    canonical_route_shape: str | None
    concrete_path: str
    http_status: int
    response_excerpt: str
    response_sha256: str
    truncated: bool

    def public(self) -> dict[str, object]:
        return {
            "action_id": str(self.action_id),
            "evidence_id": str(self.evidence_id),
            "sequence": self.sequence,
            "identity_id": str(self.identity_id) if self.identity_id else None,
            "method": self.method,
            "canonical_route_shape": self.canonical_route_shape,
            "concrete_path": self.concrete_path,
            "http_status": self.http_status,
            "response_excerpt": self.response_excerpt,
            "response_sha256": self.response_sha256,
            "truncated": self.truncated,
        }


class EvidenceCatalog:
    def __init__(self, events: EventStore, state_root: Path) -> None:
        self.events = events
        self.state = StateStore(state_root)

    async def entries(
        self,
        run_id: UUID,
        instance_id: UUID,
        generation: int,
        family: str,
        *,
        task_id: UUID,
        worker_id: UUID | None,
    ) -> tuple[CatalogEntry, ...]:
        trace = await self.events.read_run(run_id)
        terminal = min(
            (e.sequence_number for e in trace if isinstance(e, RunCompleted)),
            default=float("inf"),
        )
        candidates = [
            e
            for e in trace
            if isinstance(e, ActionRequested)
            and e.source_phase is None
            and e.originating_call_id is not None
            and e.worker_id == worker_id
            and e.task_id == task_id
            and e.range_instance_id == instance_id
            and e.range_generation == generation
            and e.sequence_number < terminal
        ]
        requested = {e.action_id: e for e in candidates}
        if len(requested) != len(candidates):
            raise ValueError("duplicate model action ID")
        result: list[CatalogEntry] = []
        for done in trace:
            if (
                not isinstance(done, ActionCompleted)
                or done.sequence_number >= terminal
                or done.evidence_id is None
                or done.action_id not in requested
            ):
                continue
            start = requested[done.action_id]
            if done.causation_id != start.event_id or done.sequence_number <= start.sequence_number:
                raise ValueError("action event binding mismatch")
            directory = self.state.instance_dir(instance_id)
            evidence_path = directory / "evidence" / f"{done.evidence_id.hex}.json"
            if evidence_path.is_symlink():
                raise ValueError("unsafe evidence artifact")
            evidence = Evidence.model_validate_json(evidence_path.read_bytes())
            request_path = directory / "requests" / f"{evidence.request_artifact_id.hex}.json"
            if request_path.is_symlink():
                raise ValueError("unsafe request artifact")
            request = RequestArtifact.model_validate_json(request_path.read_bytes())
            if (
                request.run_id != run_id
                or evidence.run_id != run_id
                or request.range_instance_id != instance_id
                or evidence.range_instance_id != instance_id
                or request.range_generation != generation
                or evidence.range_generation != generation
                or request.action_id != done.action_id
                or evidence.action_id != done.action_id
                or request.request_artifact_id != evidence.request_artifact_id
                or request.source_phase is not None
                or request.originating_call_id != start.originating_call_id
                or request.originating_tool_call_id != start.originating_tool_call_id
                or request.task_id != task_id
                or request.worker_id != worker_id
                or evidence.task_id != task_id
                or evidence.worker_id != worker_id
                or request.destination != family
                or start.destination != family
                or start.request_artifact_id != request.request_artifact_id
                or start.path_sha256 != hashlib.sha256(request.path.encode()).hexdigest()
                or start.method != request.method
                or start.identity_id != request.identity_id
                or evidence.identity_id != request.identity_id
                or done.evidence_id != evidence.evidence_id
                or done.http_status != evidence.http_status
                or done.response_sha256 != evidence.response_sha256
            ):
                raise ValueError("request/evidence provenance mismatch")
            body = base64.b64decode(evidence.body_b64, validate=True).decode("utf-8", "replace")
            result.append(
                CatalogEntry(
                    action_id=done.action_id,
                    evidence_id=evidence.evidence_id,
                    sequence=done.sequence_number,
                    identity_id=request.identity_id,
                    method=request.method,
                    canonical_route_shape=canonical_route(family, request.method, request.path),
                    concrete_path=request.path,
                    http_status=evidence.http_status,
                    response_excerpt=body[:320],
                    response_sha256=evidence.response_sha256,
                    truncated=evidence.truncated or len(body) > 320,
                )
            )
        return tuple(sorted(result, key=lambda e: e.sequence))


def select_entries(
    entries: tuple[CatalogEntry, ...],
    *,
    identity_id: UUID | None = None,
    method: str | None = None,
    route_shape: str | None = None,
    sequence_start: int | None = None,
    sequence_end: int | None = None,
    status_code: int | None = None,
    max_chars: int = 2000,
) -> list[dict[str, object]]:
    selected = [
        entry.public()
        for entry in entries
        if (identity_id is None or entry.identity_id == identity_id)
        and (method is None or entry.method == method)
        and (route_shape is None or entry.canonical_route_shape == route_shape)
        and (sequence_start is None or entry.sequence >= sequence_start)
        and (sequence_end is None or entry.sequence <= sequence_end)
        and (status_code is None or entry.http_status == status_code)
    ]
    result: list[dict[str, object]] = []
    for entry in selected[:20]:
        if len(json.dumps([*result, entry], separators=(",", ":"))) > max_chars:
            break
        result.append(entry)
    return result


def preflight_finding(
    arguments_json: str,
    schema: type[Any],
    entries: tuple[CatalogEntry, ...],
    family: str,
    *,
    max_chars: int = 2000,
) -> dict[str, object]:
    """Check syntax and citations; never inspect an oracle or judge a claim."""
    try:
        raw = json.loads(arguments_json)
    except (ValueError, TypeError):
        return {"ok": False, "errors": ["malformed_finding_schema"]}
    if not isinstance(raw, dict):
        return {"ok": False, "errors": ["malformed_finding_schema"]}
    errors: list[str] = []
    action = raw.get("action")
    if isinstance(action, str):
        words = action.split(" ", 1)
        if len(words) != 2 or words[0] not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            errors.append("noncanonical_route")
        elif action not in range_surface(family).routes:
            errors.append("noncanonical_route")
    try:
        parsed = schema.model_validate(raw)
    except ValidationError:
        errors.append("malformed_finding_schema")
        parsed = None
    by_evidence = {item.evidence_id: item for item in entries}
    by_action = {item.action_id: item for item in entries}
    citations = raw.get("evidence")
    autofill: list[dict[str, object]] = []
    seen: set[str] = set()
    if isinstance(citations, list):
        for citation in citations:
            if not isinstance(citation, dict):
                errors.append("malformed_finding_schema")
                continue
            try:
                evidence_id = UUID(str(citation.get("evidence_id")))
                action_id = UUID(str(citation.get("action_id")))
            except (ValueError, TypeError):
                errors.append("malformed_finding_schema")
                continue
            if str(evidence_id) in seen:
                errors.append("duplicate_citation_id")
            seen.add(str(evidence_id))
            match = by_evidence.get(evidence_id)
            if match is None:
                errors.append("evidence_id_not_found")
                continue
            if action_id not in by_action:
                errors.append("wrong_run")
            elif match.action_id != action_id:
                errors.append("action_evidence_mismatch")
            autofill.append(
                {
                    "evidence_id": str(match.evidence_id),
                    "action_id": str(match.action_id),
                    "identity_id": str(match.identity_id) if match.identity_id else None,
                    "canonical_route_shape": match.canonical_route_shape,
                    "sequence": match.sequence,
                }
            )
    else:
        errors.append("malformed_finding_schema")
    if parsed is not None and isinstance(action, str):
        if action not in {item.canonical_route_shape for item in entries}:
            errors.append("noncanonical_route")
    result: dict[str, object] = {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "citation_facts": [],
    }
    facts: list[dict[str, object]] = []
    for fact in autofill:
        if len(json.dumps({**result, "citation_facts": [*facts, fact]})) > max_chars:
            break
        facts.append(fact)
    result["citation_facts"] = facts
    result["citation_facts_truncated"] = len(facts) != len(autofill)
    return result
