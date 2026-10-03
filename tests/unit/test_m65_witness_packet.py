"""The reporter sees only closed action evidence from its bound run."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from offsecgym.research.m65_witness_packet import ReadOnlyReporterTools, build_reporter_bundle
from offsecgym.schemas.domain import (
    AuthorizationExpectation,
    EvidenceRef,
    FindingProposal,
    ValidationResult,
)
from offsecgym.schemas.events import (
    ActionBlocked,
    ActionCompleted,
    ActionRequested,
    FindingValidated,
    RangeStarted,
    RunStarted,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact


def _action(
    root: Path,
    run_id: UUID,
    instance_id: UUID,
    *,
    target: UUID,
    response_id: UUID,
    sequence: int,
) -> tuple[ActionRequested, ActionCompleted, UUID]:
    action_id, request_id, evidence_id = uuid4(), uuid4(), uuid4()
    path = f"/api/documents/{target}"
    raw = json.dumps({"id": str(response_id), "note": "trusted response"}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    request = RequestArtifact(
        request_artifact_id=request_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        destination="saas",
        method="GET",
        path=path,
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        request_artifact_id=request_id,
        http_status=200,
        body_b64=base64.b64encode(raw).decode(),
        response_sha256=digest,
    )
    instance = root / "instances" / instance_id.hex
    (instance / "requests").mkdir(parents=True, exist_ok=True)
    (instance / "evidence").mkdir(parents=True, exist_ok=True)
    (instance / "requests" / f"{request_id.hex}.json").write_text(request.model_dump_json())
    (instance / "evidence" / f"{evidence_id.hex}.json").write_text(evidence.model_dump_json())
    return (
        ActionRequested(
            run_id=run_id,
            actor="gateway",
            sequence_number=sequence,
            action_id=action_id,
            action_type="http_request",
            destination="saas",
            method="GET",
            path_sha256=hashlib.sha256(path.encode()).hexdigest(),
            range_instance_id=instance_id,
            range_generation=0,
            request_artifact_id=request_id,
        ),
        ActionCompleted(
            run_id=run_id,
            actor="gateway",
            sequence_number=sequence + 1,
            action_id=action_id,
            evidence_id=evidence_id,
            duration_ms=1,
            http_status=200,
            response_sha256=digest,
        ),
        evidence_id,
    )


def _trace(root: Path):
    run_id, instance_id, build_id = uuid4(), uuid4(), uuid4()
    first_target, first_response, second_target = uuid4(), uuid4(), uuid4()
    first = _action(
        root,
        run_id,
        instance_id,
        target=first_target,
        response_id=first_response,
        sequence=3,
    )
    second = _action(
        root,
        run_id,
        instance_id,
        target=second_target,
        response_id=second_target,
        sequence=5,
    )
    trace = [
        RunStarted(run_id=run_id, actor="controller", sequence_number=1, experiment_hash="0" * 64),
        RangeStarted(
            run_id=run_id,
            actor="controller",
            sequence_number=2,
            build_id=build_id,
            range_instance_id=instance_id,
            range_generation=0,
        ),
        first[0],
        first[1],
        second[0],
        second[1],
    ]
    return run_id, trace, first, first_target, first_response, second_target


def test_reporter_packet_preserves_action_order_and_entity_association(tmp_path: Path) -> None:
    run_id, trace, first, target, response, second_target = _trace(tmp_path)
    oracle = tmp_path / "oracles" / "hidden.json"
    oracle.parent.mkdir()
    oracle.write_text("SECRET_ORACLE_SENTINEL")
    bundle = build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    assert len(bundle.packet.actions) == 2
    assert bundle.packet.actions[0].route_target_id == target
    assert bundle.packet.actions[0].response_object_id == response
    assert bundle.packet.actions[1].route_target_id == second_target
    assert bundle.packet.actions[1].response_object_id == second_target
    assert "SECRET_ORACLE_SENTINEL" not in bundle.packet.model_dump_json()
    assert "trusted response" in bundle.get_action(first[0].action_id)["response_body"]
    assert not hasattr(bundle, "execute")
    assert (
        bundle.packet.bundle_sha256
        == build_reporter_bundle(trace, tmp_path, expected_run_id=run_id).packet.bundle_sha256
    )
    serialized = json.dumps(bundle.packet.model_dump(mode="json"))
    for forbidden in ("SECRET_ORACLE_SENTINEL", "matched_root_cause_id", "score_valid"):
        assert forbidden not in serialized
    assert bundle.get_evidence(first[2])["action_id"] == str(first[0].action_id)
    assert bundle.get_entity(target)["entity_id"] == str(target)


def test_reporter_rejects_foreign_evidence_and_tampered_artifacts(tmp_path: Path) -> None:
    run_id, trace, first, target, _, _ = _trace(tmp_path)
    bundle = build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    property_ = AuthorizationExpectation(
        subject_role="member",
        action="read",
        resource_type="document",
        object_relation="cross_tenant",
        expected="deny",
    )
    proposal = FindingProposal(
        claim="Cross-tenant document read",
        family="object_authorization",
        asset_id=target,
        security_property=property_,
        evidence=(
            EvidenceRef(action_id=first[0].action_id, evidence_id=first[2], description="read"),
        ),
    )
    bundle.check_proposal(proposal)
    with pytest.raises(ValueError, match="outside"):
        bundle.get_action(uuid4())
    with pytest.raises(ValueError, match="outside"):
        bundle.check_proposal(
            proposal.model_copy(
                update={
                    "evidence": (
                        EvidenceRef(
                            action_id=first[0].action_id,
                            evidence_id=uuid4(),
                            description="foreign",
                        ),
                    )
                }
            )
        )
    evidence_path = (
        tmp_path
        / "instances"
        / trace[1].range_instance_id.hex
        / "evidence"
        / f"{first[2].hex}.json"
    )
    data = json.loads(evidence_path.read_text())
    data["body_b64"] = base64.b64encode(b"changed").decode()
    evidence_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="hash mismatch"):
        build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)


def test_reporter_rejects_wrong_run_and_reordered_events(tmp_path: Path) -> None:
    run_id, trace, _, _, _, _ = _trace(tmp_path)
    with pytest.raises(ValueError, match="one versioned run"):
        build_reporter_bundle(trace, tmp_path, expected_run_id=uuid4())
    with pytest.raises(ValueError, match="ordered"):
        build_reporter_bundle(
            [*trace[:3], trace[4], trace[3], trace[5]], tmp_path, expected_run_id=run_id
        )
    validation = FindingValidated(
        run_id=run_id,
        actor="validator",
        sequence_number=7,
        result=ValidationResult(
            run_id=run_id, finding_id=uuid4(), status="rejected", reason_codes=("property_patched",)
        ),
    )
    with pytest.raises(ValueError, match="before validation"):
        build_reporter_bundle([*trace, validation], tmp_path, expected_run_id=run_id)


def test_bundle_hash_changes_with_ordered_evidence(tmp_path: Path) -> None:
    run_id, trace, _, _, _, _ = _trace(tmp_path)
    first = build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    reordered = [
        trace[0],
        trace[1],
        trace[4].model_copy(update={"sequence_number": 3}),
        trace[5].model_copy(update={"sequence_number": 4}),
        trace[2].model_copy(update={"sequence_number": 5}),
        trace[3].model_copy(update={"sequence_number": 6}),
    ]
    second = build_reporter_bundle(reordered, tmp_path, expected_run_id=run_id)
    assert first.packet.bundle_sha256 != second.packet.bundle_sha256
    assert first.packet.actions[0].action_id != second.packet.actions[0].action_id


def test_blocked_request_is_visible_but_cannot_be_cited(tmp_path: Path) -> None:
    run_id, trace, _, _, _, _ = _trace(tmp_path)
    instance_id = trace[1].range_instance_id
    action_id, request_id = uuid4(), uuid4()
    path = "/api/invoices/" + str(uuid4()) + "/refund"
    request = RequestArtifact(
        request_artifact_id=request_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        destination="saas",
        method="POST",
        path=path,
    )
    request_path = tmp_path / "instances" / instance_id.hex / "requests" / f"{request_id.hex}.json"
    request_path.write_text(request.model_dump_json())
    requested = ActionRequested(
        run_id=run_id,
        actor="gateway",
        sequence_number=7,
        action_id=action_id,
        action_type="http_request",
        destination="saas",
        method="POST",
        path_sha256=hashlib.sha256(path.encode()).hexdigest(),
        range_instance_id=instance_id,
        range_generation=0,
        request_artifact_id=request_id,
    )
    blocked = ActionBlocked(
        run_id=run_id,
        actor="gateway",
        sequence_number=8,
        action_id=action_id,
        reason_code="budget_exhausted",
    )
    bundle = build_reporter_bundle([*trace, requested, blocked], tmp_path, expected_run_id=run_id)
    assert len(bundle.packet.unobserved_attempts) == 1
    assert bundle.packet.unobserved_attempts[0].terminal_status == "blocked"
    assert bundle.packet.unobserved_attempts[0].path == path
    with pytest.raises(ValueError, match="outside"):
        bundle.get_action(action_id)
    with pytest.raises(ValueError, match="one terminal"):
        build_reporter_bundle([*trace, requested], tmp_path, expected_run_id=run_id)


@pytest.mark.asyncio
async def test_reporter_tools_expose_only_evidence_lookup_and_bound_submission(
    tmp_path: Path,
) -> None:
    run_id, trace, first, target, _, _ = _trace(tmp_path)
    bundle = build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    sink = AsyncMock()
    tools = ReadOnlyReporterTools(bundle, sink)
    assert not hasattr(tools, "execute")
    assert tools.get_action_evidence(first[0].action_id)["evidence_id"] == str(first[2])
    proposal = FindingProposal(
        claim="Read crossed a tenant boundary",
        family="object_authorization",
        asset_id=target,
        security_property=AuthorizationExpectation(
            subject_role="member",
            action="read",
            resource_type="document",
            object_relation="cross_tenant",
            expected="deny",
        ),
        evidence=(
            EvidenceRef(action_id=first[0].action_id, evidence_id=first[2], description="read"),
        ),
    )
    await tools.submit_finding(proposal)
    sink.submit.assert_awaited_once_with(proposal)
