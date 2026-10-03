"""Rebuild a bounded monolithic working set from audited bootstrap artifacts."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from offsecgym.interfaces import EventStore
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    PrerequisiteBootstrapCompleted,
    WorldFactSubmitted,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.solver.scripted import ExperimentInfrastructureError
from offsecgym.worldview.working_set import ActiveWorkingSet


async def bootstrap_working_set(
    events: EventStore, state_root: Path, run_id: UUID
) -> ActiveWorkingSet:
    """Expose checked GETs and cited controller facts, never hidden fixtures."""
    trace = await events.read_run(run_id)
    markers = [event for event in trace if isinstance(event, PrerequisiteBootstrapCompleted)]
    requests = [
        event
        for event in trace
        if isinstance(event, ActionRequested) and event.source_phase == "bootstrap"
    ]
    completed = {event.action_id: event for event in trace if isinstance(event, ActionCompleted)}
    facts = defaultdict(list)
    for event in trace:
        if isinstance(event, WorldFactSubmitted) and event.actor == "controller":
            for action_id in event.fact.source_action_ids:
                facts[action_id].append(event.fact)
    if len(markers) != 1 or len(requests) != markers[0].action_count:
        raise ExperimentInfrastructureError("bootstrap_context_event_mismatch")
    working_set = ActiveWorkingSet()
    for requested in requests:
        done = completed.get(requested.action_id)
        if (
            done is None
            or done.sequence_number >= markers[0].sequence_number
            or requested.range_instance_id is None
            or requested.request_artifact_id is None
            or requested.range_generation is None
            or done.evidence_id is None
            or done.http_status != 200
        ):
            raise ExperimentInfrastructureError("bootstrap_context_action_mismatch")
        instance = state_root / "instances" / requested.range_instance_id.hex
        request_path = instance / "requests" / f"{requested.request_artifact_id.hex}.json"
        evidence_path = instance / "evidence" / f"{done.evidence_id.hex}.json"
        try:
            artifact = RequestArtifact.model_validate_json(request_path.read_bytes())
            evidence = Evidence.model_validate_json(evidence_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ExperimentInfrastructureError("bootstrap_context_artifact_missing") from exc
        if (
            artifact.run_id != run_id
            or artifact.action_id != requested.action_id
            or artifact.request_artifact_id != requested.request_artifact_id
            or artifact.range_instance_id != requested.range_instance_id
            or artifact.range_generation != requested.range_generation
            or artifact.source_phase != "bootstrap"
            or artifact.identity_id != requested.identity_id
            or artifact.method != "GET"
            or artifact.json_body is not None
            or hashlib.sha256(artifact.path.encode()).hexdigest() != requested.path_sha256
            or evidence.run_id != run_id
            or evidence.action_id != requested.action_id
            or evidence.evidence_id != done.evidence_id
            or evidence.request_artifact_id != artifact.request_artifact_id
            or evidence.range_instance_id != artifact.range_instance_id
            or evidence.range_generation != artifact.range_generation
            or evidence.http_status != done.http_status
            or evidence.response_sha256 != done.response_sha256
        ):
            raise ExperimentInfrastructureError("bootstrap_context_provenance_mismatch")
        request = ActionRequest(
            action_id=artifact.action_id,
            run_id=run_id,
            source_phase="bootstrap",
            identity_id=artifact.identity_id,
            kind="http_request",
            destination=artifact.destination,
            method="GET",
            path=artifact.path,
        )
        result = ActionResult(
            action_id=done.action_id,
            status="completed",
            evidence_id=done.evidence_id,
            duration_ms=done.duration_ms,
            http_status=done.http_status,
            response_sha256=done.response_sha256,
        )
        try:
            working_set.observe(request, result, tuple(facts[requested.action_id]))
        except ValueError as exc:
            raise ExperimentInfrastructureError("bootstrap_context_fact_mismatch") from exc
    return working_set
