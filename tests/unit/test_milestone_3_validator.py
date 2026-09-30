"""Independent validator rejects broken provenance before consulting proof semantics."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml
from pydantic import ValidationError

from offsecgym.evaluation import evaluate_run
from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.manifests import write_json_atomic
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import (
    CandidateFinding,
    EvidenceRef,
    ExperimentContext,
    FindingProposal,
    ReplayTraceRef,
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
from offsecgym.schemas.specs import Budget, RangeSpec
from offsecgym.validation import DeterministicValidator
from offsecgym.validation.deterministic import ReplayOutcome


class MemoryEvents:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        stored = event.model_copy(update={"sequence_number": len(self.items) + 1})
        self.items.append(stored)
        return stored

    async def read_run(self, run_id: UUID):
        return [item for item in self.items if item.run_id == run_id]


async def document_case(
    tmp_path: Path,
    *,
    patched: bool = False,
    account_index: int = 0,
    document_index: int = 1,
):
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
    account = fixture["accounts"][account_index]
    document = fixture["documents"][document_index]
    action_id, artifact_id, evidence_id = uuid4(), uuid4(), uuid4()
    path = f"/api/documents/{document['id']}"
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
        asset_id=UUID(document["id"]),
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
async def test_alternate_foreign_witness_maps_to_same_root_cause(tmp_path: Path) -> None:
    canonical, first, context, oracle, _, _ = await document_case(tmp_path / "canonical")
    alternate, second, other_context, other_oracle, _, _ = await document_case(
        tmp_path / "alternate", account_index=4, document_index=0
    )
    first_result = await canonical.validate(first, context)
    second_result = await alternate.validate(second, other_context)
    assert first_result.status == second_result.status == "validated"
    assert first_result.matched_root_cause_id == second_result.matched_root_cause_id
    assert first_result.matched_property_id == second_result.matched_property_id
    assert oracle.pair_id == other_oracle.pair_id
    own, candidate, own_context, _, _, _ = await document_case(
        tmp_path / "own", account_index=0, document_index=0
    )
    assert (await own.validate(candidate, own_context)).reason_codes == ("proof_missing",)


@pytest.mark.asyncio
async def test_public_preview_accepts_another_invoice_witness(tmp_path: Path) -> None:
    validator, finding, context, oracle, events, evidence_path = await document_case(tmp_path)
    invoice = json.loads(
        (validator.state.build_dir(context.build_id) / "fixture.json").read_text()
    )["invoices"][0]
    public_property = next(
        prop for prop in oracle.properties if prop.expectation.kind == "field_exposure"
    )
    request_path = (
        validator.state.instance_dir(context.range_instance_id)
        / "requests"
        / f"{json.loads(evidence_path.read_text())['request_artifact_id'].replace('-', '')}.json"
    )
    request = json.loads(request_path.read_text())
    request["identity_id"] = None
    request["path"] = f"/api/public/invoices/{invoice['id']}/preview"
    write_json_atomic(request_path, request)
    response = json.dumps({"id": invoice["id"], "billing_email": invoice["billing_email"]}).encode()
    evidence = json.loads(evidence_path.read_text())
    evidence["identity_id"] = None
    evidence["body_b64"] = base64.b64encode(response).decode()
    evidence["response_sha256"] = hashlib.sha256(response).hexdigest()
    write_json_atomic(evidence_path, evidence)
    events.items[0] = events.items[0].model_copy(
        update={
            "identity_id": None,
            "path_sha256": hashlib.sha256(request["path"].encode()).hexdigest(),
        }
    )
    events.items[1] = events.items[1].model_copy(
        update={"response_sha256": evidence["response_sha256"]}
    )
    alternate = finding.model_copy(
        update={
            "family": public_property.family,
            "asset_id": UUID(invoice["id"]),
            "security_property": public_property.expectation,
        }
    )
    result = await validator.validate(alternate, context)
    assert result.status == "validated"
    assert result.matched_root_cause_id == public_property.root_cause_id


@pytest.mark.asyncio
async def test_another_member_refund_uses_actual_asset_and_replay(tmp_path: Path) -> None:
    validator, _, context, oracle, events, _ = await document_case(tmp_path)
    fixture = json.loads((validator.state.build_dir(context.build_id) / "fixture.json").read_text())
    account = fixture["accounts"][2]
    invoice = fixture["invoices"][1]
    invoice_id = UUID(invoice["id"])
    identity_id = UUID(account["id"])
    prop = next(item for item in oracle.properties if item.expectation.kind == "state_transition")
    references = []
    cases = (
        ("GET", f"/api/invoices/{invoice_id}", "paid", None),
        (
            "POST",
            f"/api/invoices/{invoice_id}/refund",
            "refunded",
            {"reason": "duplicate charge"},
        ),
        ("GET", f"/api/invoices/{invoice_id}", "refunded", None),
    )
    for method, path, state, request_body in cases:
        action_id, artifact_id, evidence_id = uuid4(), uuid4(), uuid4()
        request = RequestArtifact(
            request_artifact_id=artifact_id,
            run_id=context.run_id,
            action_id=action_id,
            range_instance_id=context.range_instance_id,
            range_generation=context.range_generation,
            identity_id=identity_id,
            destination="saas",
            method=method,
            path=path,
            json_body=request_body,
        )
        response_body = json.dumps({"id": str(invoice_id), "status": state}).encode()
        evidence = Evidence(
            evidence_id=evidence_id,
            run_id=context.run_id,
            action_id=action_id,
            range_instance_id=context.range_instance_id,
            range_generation=context.range_generation,
            request_artifact_id=artifact_id,
            identity_id=identity_id,
            http_status=200,
            body_b64=base64.b64encode(response_body).decode(),
            response_sha256=hashlib.sha256(response_body).hexdigest(),
        )
        root = validator.state.instance_dir(context.range_instance_id)
        write_json_atomic(
            root / "requests" / f"{artifact_id.hex}.json", request.model_dump(mode="json")
        )
        write_json_atomic(
            root / "evidence" / f"{evidence_id.hex}.json", evidence.model_dump(mode="json")
        )
        requested = await events.append(
            ActionRequested(
                run_id=context.run_id,
                actor="gateway",
                action_id=action_id,
                action_type="http_request",
                destination="saas",
                method=method,
                path_sha256=hashlib.sha256(path.encode()).hexdigest(),
                body_sha256=(
                    hashlib.sha256(
                        json.dumps(request_body, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    if request_body is not None
                    else None
                ),
                identity_id=identity_id,
                range_instance_id=context.range_instance_id,
                range_generation=context.range_generation,
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
        references.append(
            EvidenceRef(evidence_id=evidence_id, action_id=action_id, description=path)
        )

    class Replay:
        async def verify_transition(self, replay_context, matched_prop, identity, asset):
            assert replay_context == context
            assert matched_prop.property_id == prop.property_id
            assert identity == identity_id and asset == invoice_id
            return ReplayOutcome(
                "validated",
                ReplayTraceRef(
                    replay_run_id=uuid4(),
                    range_instance_id=uuid4(),
                    range_generation=0,
                    evidence_ids=(uuid4(), uuid4(), uuid4()),
                ),
            )

    validator.replay = Replay()
    finding = CandidateFinding(
        finding_id=uuid4(),
        run_id=context.run_id,
        range_instance_id=context.range_instance_id,
        range_generation=context.range_generation,
        claim="Member refunded own invoice",
        family=prop.family,
        asset_id=invoice_id,
        security_property=prop.expectation,
        evidence=tuple(references),
    )
    result = await validator.validate(finding, context)
    assert result.status == "validated"
    assert result.matched_root_cause_id == prop.root_cause_id
    assert result.replay_trace is not None


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
        run_id=finding.run_id,
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
    rejected = ValidationResult(run_id=third.run_id, finding_id=third.finding_id, status="rejected")
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
    with pytest.raises(ValueError, match="same-run validation"):
        evaluate_run(
            (finding,),
            (validated.model_copy(update={"run_id": uuid4()}),),
            oracle,
        )
    with pytest.raises(ValueError, match="inconclusive validation"):
        evaluate_run(
            (finding,),
            (
                ValidationResult(
                    run_id=finding.run_id, finding_id=finding.finding_id, status="inconclusive"
                ),
            ),
            oracle,
        )


def test_finding_event_round_trip(tmp_path: Path) -> None:
    import asyncio

    _, finding, _, _, _, _ = asyncio.run(document_case(tmp_path))
    event = FindingSubmitted(run_id=finding.run_id, actor="solver", finding=finding)
    assert parse_event(event.model_dump(mode="json")) == event
    result = ValidationResult(
        run_id=finding.run_id, finding_id=finding.finding_id, status="rejected"
    )
    checked = FindingValidated(run_id=finding.run_id, actor="validator", result=result)
    assert parse_event(checked.model_dump(mode="json")) == checked
    with pytest.raises(ValidationError, match="finding submission run"):
        FindingSubmitted(run_id=uuid4(), actor="solver", finding=finding)
    with pytest.raises(ValidationError, match="validation result run"):
        FindingValidated(run_id=uuid4(), actor="validator", result=result)
    legacy_result = ValidationResult(
        schema_version="2", finding_id=finding.finding_id, status="rejected"
    )
    legacy_event = FindingValidated(
        schema_version="2", run_id=finding.run_id, actor="validator", result=legacy_result
    )
    assert parse_event(legacy_event.model_dump(mode="json")) == legacy_event


@pytest.mark.asyncio
async def test_controller_binds_finding_proposal_and_checks_action_scope(tmp_path: Path) -> None:
    _, finding, context, _, events, _ = await document_case(tmp_path)
    bound = BoundFindingSink(
        events,
        ExperimentContext(
            run_id=context.run_id,
            range_instance_id=context.range_instance_id,
            range_generation=context.range_generation,
            budget=Budget(max_actions=10),
        ),
    )
    proposal = FindingProposal(
        claim=finding.claim,
        family=finding.family,
        asset_id=finding.asset_id,
        security_property=finding.security_property,
        evidence=finding.evidence,
    )
    with pytest.raises(ValidationError):
        FindingProposal.model_validate({**proposal.model_dump(), "run_id": uuid4()})
    submitted = await bound.submit(proposal)
    assert submitted.run_id == context.run_id
    assert submitted.range_instance_id == context.range_instance_id
    assert submitted.range_generation == context.range_generation
    assert submitted.finding_id != finding.finding_id
    assert (await bound.read_run(context.run_id)) == (submitted,)
    forged = proposal.model_copy(
        update={"evidence": (proposal.evidence[0].model_copy(update={"evidence_id": uuid4()}),)}
    )
    with pytest.raises(ValueError, match="outside the bound run"):
        await bound.submit(forged)
