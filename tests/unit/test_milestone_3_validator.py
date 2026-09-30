"""Independent validator rejects broken provenance before consulting proof semantics."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from offsecgym.evaluation import evaluate_run
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.manifests import write_json_atomic
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import (
    CandidateFinding,
    EvidenceRef,
    ValidationContext,
    ValidationResult,
)
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    parse_event,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.specs import RangeSpec
from offsecgym.validation import DeterministicValidator


class MemoryEvents:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        stored = event.model_copy(update={"sequence_number": len(self.items) + 1})
        self.items.append(stored)
        return stored

    async def read_run(self, run_id: UUID):
        return [item for item in self.items if item.run_id == run_id]


async def document_case(tmp_path: Path, *, patched: bool = False):
    raw = yaml.safe_load((Path(__file__).parents[2] / "examples" / "saas-range.yaml").read_text())
    raw["patched"] = patched
    runtime = ComposeRangeRuntime(tmp_path)
    build_id = await runtime.build(RangeSpec.model_validate(raw))
    instance_id = await runtime.create_instance(build_id)
    context = ValidationContext(
        run_id=uuid4(), build_id=build_id, range_instance_id=instance_id, range_generation=0
    )
    oracle = StateOracleStore(runtime.state).load_for_context(context)
    prop = next(item for item in oracle.properties if item.object.resource_type == "document")
    fixture = json.loads((runtime.state.build_dir(build_id) / "fixture.json").read_text())
    account = fixture["accounts"][0]
    document = fixture["documents"][1]
    action_id, artifact_id, evidence_id = uuid4(), uuid4(), uuid4()
    path = f"/api/documents/{prop.object.object_id}"
    request = RequestArtifact(
        request_artifact_id=artifact_id,
        run_id=context.run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        identity_id=UUID(account["id"]),
        destination="saas",
        method="GET",
        path=path,
    )
    body = json.dumps(document).encode()
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=context.run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        request_artifact_id=artifact_id,
        identity_id=UUID(account["id"]),
        http_status=200,
        body_b64=base64.b64encode(body).decode(),
        response_sha256=hashlib.sha256(body).hexdigest(),
    )
    request_path = runtime.state.instance_dir(instance_id) / "requests" / f"{artifact_id.hex}.json"
    evidence_path = runtime.state.instance_dir(instance_id) / "evidence" / f"{evidence_id.hex}.json"
    write_json_atomic(request_path, request.model_dump(mode="json"))
    write_json_atomic(evidence_path, evidence.model_dump(mode="json"))
    events = MemoryEvents()
    requested = await events.append(
        ActionRequested(
            run_id=context.run_id,
            actor="gateway",
            action_id=action_id,
            action_type="http_request",
            destination="saas",
            method="GET",
            path_sha256=hashlib.sha256(path.encode()).hexdigest(),
            identity_id=UUID(account["id"]),
            range_instance_id=instance_id,
            range_generation=0,
            request_artifact_id=artifact_id,
        )
    )
    await events.append(
        ActionCompleted(
            run_id=context.run_id,
            actor="gateway",
            action_id=action_id,
            evidence_id=evidence_id,
            duration_ms=0,
            http_status=200,
            response_sha256=evidence.response_sha256,
            causation_id=requested.event_id,
        )
    )
    finding = CandidateFinding(
        finding_id=uuid4(),
        run_id=context.run_id,
        range_instance_id=instance_id,
        range_generation=0,
        claim="Member read a foreign document",
        family=prop.family,
        asset_id=prop.object.object_id,
        security_property=prop.expectation,
        evidence=(EvidenceRef(evidence_id=evidence_id, action_id=action_id, description="read"),),
    )
    validator = DeterministicValidator(runtime.state, events, StateOracleStore(runtime.state))
    return validator, finding, context, oracle, events, evidence_path


@pytest.mark.asyncio
async def test_valid_document_proof_and_patched_rejection(tmp_path: Path) -> None:
    validator, finding, context, oracle, _, _ = await document_case(tmp_path / "vulnerable")
    result = await validator.validate(finding, context)
    assert result.status == "validated"
    assert result.matched_property_id in oracle.active_property_ids
    assert result.matched_root_cause_id is not None
    patched, candidate, patched_context, _, _, _ = await document_case(
        tmp_path / "patched", patched=True
    )
    rejected = await patched.validate(candidate, patched_context)
    assert rejected.status == "rejected" and rejected.reason_codes == ("property_patched",)


@pytest.mark.asyncio
async def test_provenance_closure_rejects_cross_run_and_action_spoofing(tmp_path: Path) -> None:
    validator, finding, context, _, events, evidence_path = await document_case(tmp_path)

    class GuardOracle:
        def load_for_context(self, _):
            raise AssertionError("provenance failure must not read hidden ground truth")

    validator.oracle_store = GuardOracle()
    mismatched = finding.model_copy(update={"run_id": uuid4()})
    assert (await validator.validate(mismatched, context)).reason_codes == ("context_mismatch",)
    ref = finding.evidence[0].model_copy(update={"action_id": uuid4()})
    mismatched = finding.model_copy(update={"evidence": (ref,)})
    assert (await validator.validate(mismatched, context)).reason_codes == (
        "evidence_action_mismatch",
    )
    payload = json.loads(evidence_path.read_text())
    payload["range_generation"] = 1
    write_json_atomic(evidence_path, payload)
    assert (await validator.validate(finding, context)).reason_codes == (
        "evidence_context_mismatch",
    )
    payload["range_generation"] = 0
    write_json_atomic(evidence_path, payload)
    events.items.pop()
    assert (await validator.validate(finding, context)).reason_codes == (
        "action_event_missing_or_duplicate",
    )


@pytest.mark.asyncio
async def test_proof_requires_matching_response_and_request(tmp_path: Path) -> None:
    validator, finding, context, _, events, evidence_path = await document_case(tmp_path)
    payload = json.loads(evidence_path.read_text())
    body = json.dumps({"id": str(uuid4()), "workspace_id": str(uuid4())}).encode()
    payload["body_b64"] = base64.b64encode(body).decode()
    payload["response_sha256"] = hashlib.sha256(body).hexdigest()
    write_json_atomic(evidence_path, payload)
    events.items[-1] = events.items[-1].model_copy(
        update={"response_sha256": payload["response_sha256"]}
    )
    assert (await validator.validate(finding, context)).reason_codes == ("proof_missing",)


def test_evaluator_deduplicates_roots_and_excludes_infrastructure_failures(tmp_path: Path) -> None:
    import asyncio

    _, finding, _, oracle, _, _ = asyncio.run(document_case(tmp_path))
    root = next(
        prop.root_cause_id for prop in oracle.properties if prop.object.resource_type == "document"
    )
    validated = ValidationResult(
        finding_id=finding.finding_id,
        status="validated",
        matched_property_id=next(
            prop.property_id for prop in oracle.properties if prop.root_cause_id == root
        ),
        matched_root_cause_id=root,
    )
    second = finding.model_copy(update={"finding_id": uuid4()})
    duplicate = validated.model_copy(update={"finding_id": second.finding_id})
    third = finding.model_copy(update={"finding_id": uuid4()})
    rejected = ValidationResult(finding_id=third.finding_id, status="rejected")
    score = evaluate_run((finding, second, third), (validated, duplicate, rejected), oracle)
    assert (score.true_positives, score.duplicates, score.false_positives) == (1, 1, 1)
    assert score.false_negatives == 4
    assert score.precision == 0.5 and score.recall == 0.2
    with pytest.raises(ValueError, match="mismatched property/root cause"):
        evaluate_run(
            (finding,),
            (validated.model_copy(update={"matched_property_id": uuid4()}),),
            oracle,
        )


def test_finding_event_round_trip(tmp_path: Path) -> None:
    import asyncio

    _, finding, _, _, _, _ = asyncio.run(document_case(tmp_path))
    event = FindingSubmitted(run_id=finding.run_id, actor="solver", finding=finding)
    assert parse_event(event.model_dump(mode="json")) == event
    result = ValidationResult(finding_id=finding.finding_id, status="rejected")
    checked = FindingValidated(run_id=finding.run_id, actor="validator", result=result)
    assert parse_event(checked.model_dump(mode="json")) == checked
