"""A post-terminal response cannot complete a confirmatory witness."""

from __future__ import annotations

import base64
import hashlib
import json
from uuid import UUID, uuid4

import pytest

from offsecgym.research.m66_confirmatory_extract import (
    extract_confirmatory_stages,
    terminal_bounded_inputs,
)
from offsecgym.research.m66_pilot_analysis import ObservedRequest, _b1_stages
from offsecgym.runtime.enterprise import FAMILY, fixture_for_seed, oracle_for_fixture
from offsecgym.schemas.events import RangeStarted, RunCompleted, RunStarted, TraceEvent
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.validation.deterministic import ProofAction

RUN_ID = UUID("680a00c2-ea35-4a9c-89fe-477c3b164b8f")
INSTANCE_ID = UUID("e763c45e-c3a1-4a26-9c13-5346bd8942e0")


def _proof(sequence: int, method: str, path: str, actor: UUID, body: dict):
    request = RequestArtifact(
        request_artifact_id=uuid4(),
        run_id=RUN_ID,
        action_id=uuid4(),
        range_instance_id=INSTANCE_ID,
        range_generation=0,
        identity_id=actor,
        destination=FAMILY,
        method=method,
        path=path,
    )
    raw = json.dumps(body, sort_keys=True).encode()
    evidence = Evidence(
        evidence_id=uuid4(),
        run_id=RUN_ID,
        action_id=request.action_id,
        range_instance_id=INSTANCE_ID,
        range_generation=0,
        request_artifact_id=request.request_artifact_id,
        identity_id=actor,
        http_status=200,
        body_b64=base64.b64encode(raw).decode(),
        response_sha256=hashlib.sha256(raw).hexdigest(),
    )
    return ProofAction(request, evidence, sequence)


def test_after_read_beyond_provider_failed_terminal_does_not_complete_b1() -> None:
    fixture, anchors = fixture_for_seed(42)
    oracle = oracle_for_fixture(fixture, anchors, frozenset(), uuid4(), uuid4())
    prop = next(item for item in oracle.properties if item.slug == "B1-SOD")
    actor = prop.subject.identity_id
    project = prop.object.workspace_id
    asset = prop.object.object_id
    assert actor is not None and project is not None
    path = f"/api/access-requests/{asset}"
    before = _proof(
        1,
        "GET",
        path,
        actor,
        {
            "id": str(asset),
            "project_id": str(project),
            "status": "PENDING",
            "requester_id": str(actor),
        },
    )
    action = _proof(
        2,
        "POST",
        path + "/approve",
        actor,
        {
            "id": str(asset),
            "project_id": str(project),
            "status": "APPLIED",
            "requester_id": str(actor),
            "approver_id": str(actor),
        },
    )
    after = _proof(
        4,
        "GET",
        path,
        actor,
        {
            "id": str(asset),
            "project_id": str(project),
            "status": "APPLIED",
            "approver_id": str(actor),
        },
    )
    requests = [ObservedRequest(2, action.request)]
    assert _b1_stages(prop, [before, action, after], requests)["complete_witness"]

    trace = [
        RunStarted(run_id=RUN_ID, sequence_number=1, actor="test", experiment_hash="test"),
        RangeStarted(
            run_id=RUN_ID,
            sequence_number=2,
            actor="test",
            build_id=oracle.build_id,
            range_instance_id=INSTANCE_ID,
            range_generation=0,
        ),
        RunCompleted(run_id=RUN_ID, sequence_number=3, actor="test", status="provider_failed"),
        TraceEvent(run_id=RUN_ID, sequence_number=4, actor="test"),
    ]
    source, bounded_actions, bounded_requests = terminal_bounded_inputs(
        trace, [before, action, after], requests
    )
    assert len(source) == 3
    assert [item.sequence for item in bounded_actions] == [1, 2]
    assert not _b1_stages(prop, bounded_actions, bounded_requests)["complete_witness"]
    result = extract_confirmatory_stages(
        trace,
        [before, action, after],
        requests,
        oracle,
        fixture,
        family=FAMILY,
        variant="vulnerable",
        arm="control",
        seed=42,
        arm_order=("control", "witness"),
        tool_names=[],
        witness_statuses=[],
        reminder_bytes=0,
    )
    assert result["status"] == "provider_failed"
    assert result["score_valid"] is False
    assert not next(row for row in result["roots"] if row["root"] == "B1-SOD")["complete_witness"]


def test_terminal_boundary_rejects_ambiguous_event_stream() -> None:
    with pytest.raises(ValueError, match="exactly one terminal"):
        terminal_bounded_inputs([], [], [])
