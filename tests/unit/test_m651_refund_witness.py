"""Prospective transition audit agrees with deterministic validator proof semantics."""

import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_saas_compiler import load_spec

from offsecgym.research.m64_stage_ledger import _trace_proof_assets
from offsecgym.research.m65_conversion_ledger import _refund_stages
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.saas import SaasRangeCompiler, _fixture_targets
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.validation.deterministic import ProofAction, _transition_evidenced


def _case(tmp_path: Path):
    spec = load_spec().model_copy(update={"scenario": "tenant_boundary_v2", "seed": 1001})
    state = StateStore(tmp_path)
    build = SaasRangeCompiler(state).build(spec)
    oracle = GroundTruthManifest.model_validate_json(
        (tmp_path / "oracles" / build.build_id.hex / "ground_truth.json").read_bytes()
    )
    fixture = json.loads((state.build_dir(build.build_id) / "fixture.json").read_text())
    prop = next(x for x in oracle.properties if x.slug == "MEMBER-REFUND")
    member, _, _, _, invoice, _ = _fixture_targets(fixture)
    assert member["workspace_id"] == invoice["workspace_id"]
    return prop, fixture, UUID(member["id"]), UUID(invoice["id"])


def _action(
    identity: UUID | None,
    asset: UUID,
    sequence: int,
    *,
    method: str,
    status: int,
    state: str,
) -> ProofAction:
    run_id, instance_id, action_id, request_id, evidence_id = (uuid4() for _ in range(5))
    path = f"/api/invoices/{asset}" + ("/refund" if method == "POST" else "")
    request = RequestArtifact(
        request_artifact_id=request_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        identity_id=identity,
        destination="saas",
        method=method,
        path=path,
        json_body={"reason": "test"} if method == "POST" else None,
    )
    raw = json.dumps({"id": str(asset), "status": state}).encode()
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        request_artifact_id=request_id,
        identity_id=identity,
        http_status=status,
        body_b64=base64.b64encode(raw).decode(),
        response_sha256=hashlib.sha256(raw).hexdigest(),
    )
    return ProofAction(request, evidence, sequence)


def test_complete_refund_witness_and_detailed_stages(tmp_path: Path) -> None:
    prop, fixture, member, invoice = _case(tmp_path)
    before = _action(member, invoice, 1, method="GET", status=200, state="paid")
    refund = _action(member, invoice, 2, method="POST", status=200, state="refunded")
    after = _action(member, invoice, 3, method="GET", status=200, state="refunded")
    actions = [before, refund, after]
    assert _transition_evidenced(actions, refund, prop, invoice)
    assert _trace_proof_assets(prop, fixture, actions) == {str(invoice)}
    stages = _refund_stages(prop, fixture, actions)
    assert stages["successful_unauthorized_transition"]
    assert stages["same_identity_paid_before"]
    assert stages["same_identity_refunded_after"]
    assert stages["complete_ordered_witness"]


def test_refund_attempt_without_response_does_not_count_as_proof(tmp_path: Path) -> None:
    prop, fixture, member, invoice = _case(tmp_path)
    path = f"/api/invoices/{invoice}/refund"
    stages = _refund_stages(prop, fixture, [], [("POST", path, member)])
    assert stages["target_invoice_known"]
    assert stages["relevant_refund_attempted"]
    assert stages["relevant_refund_attempt_count"] == 1
    assert not stages["successful_unauthorized_transition"]
    assert not stages["complete_ordered_witness"]


@pytest.mark.parametrize(
    "defect",
    [
        "wrong_before_identity",
        "wrong_after_identity",
        "failed_post",
        "late_before",
        "different_invoice",
        "missing_after",
    ],
)
def test_refund_witness_rejects_incomplete_proof(tmp_path: Path, defect: str) -> None:
    prop, fixture, member, invoice = _case(tmp_path)
    other = next(UUID(x["id"]) for x in fixture["accounts"] if x["id"] != str(member))
    other_invoice = next(UUID(x["id"]) for x in fixture["invoices"] if x["id"] != str(invoice))
    before = _action(
        other if defect == "wrong_before_identity" else member,
        invoice,
        4 if defect == "late_before" else 1,
        method="GET",
        status=200,
        state="paid",
    )
    refund = _action(
        member,
        invoice,
        2,
        method="POST",
        status=403 if defect == "failed_post" else 200,
        state="refunded",
    )
    after = _action(
        other if defect == "wrong_after_identity" else member,
        other_invoice if defect == "different_invoice" else invoice,
        3,
        method="GET",
        status=200,
        state="refunded",
    )
    actions = [before, refund] if defect == "missing_after" else [before, refund, after]
    if defect != "failed_post":
        assert not _transition_evidenced(actions, refund, prop, invoice)
    assert not _trace_proof_assets(prop, fixture, actions)
    assert not _refund_stages(prop, fixture, actions)["complete_ordered_witness"]
