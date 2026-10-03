"""Static root-stage proof uses the same requirements as the validator."""

import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_saas_compiler import load_spec

from offsecgym.research.m64_stage_ledger import COLLECTIONS, _trace_proof_assets
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.saas import SaasRangeCompiler
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.validation.deterministic import (
    DeterministicValidator,
    ProofAction,
    _action_route,
    _relation_met,
)


def _source(tmp_path: Path):
    spec = load_spec().model_copy(update={"scenario": "tenant_boundary_v2", "seed": 1002})
    state = StateStore(tmp_path)
    build = SaasRangeCompiler(state).build(spec)
    oracle = GroundTruthManifest.model_validate_json(
        (tmp_path / "oracles" / build.build_id.hex / "ground_truth.json").read_bytes()
    )
    fixture = json.loads((state.build_dir(build.build_id) / "fixture.json").read_text())
    return oracle, fixture


def _action(
    identity: UUID | None, method: str, path: str, asset: UUID, body: dict, status: int
) -> ProofAction:
    run_id, instance_id, action_id, request_id, evidence_id = (uuid4() for _ in range(5))
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
    )
    raw = json.dumps(body).encode()
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
    return ProofAction(request, evidence, 2)


@pytest.mark.parametrize(
    "slug",
    [
        "DOC-CROSS-TENANT-READ",
        "INVOICE-CROSS-TENANT-READ",
        "TICKET-CROSS-TENANT-READ",
        "PUBLIC-INVOICE-METADATA",
    ],
)
def test_static_proof_requires_real_response_and_relation(tmp_path: Path, slug: str) -> None:
    oracle, fixture = _source(tmp_path)
    prop = next(p for p in oracle.properties if p.slug == slug)
    asset = UUID(fixture[COLLECTIONS[prop.object.resource_type]][0]["id"])
    identity = None
    if prop.subject.role != "anonymous":
        identity = next(
            UUID(account["id"])
            for account in fixture["accounts"]
            if account["role"] == prop.subject.role
            and _relation_met(fixture, prop, account, prop.expectation.object_relation, asset)
        )
    method, path = _action_route(prop, asset)
    body = {"id": str(asset)}
    if slug == "PUBLIC-INVOICE-METADATA":
        body["billing_email"] = "visible@example.test"
    good = _action(identity, method, path, asset, body, 200)
    assert DeterministicValidator._requirements_met(good, prop, fixture, asset)
    assert _trace_proof_assets(prop, fixture, [good]) == {str(asset)}
    denied = _action(identity, method, path, asset, body, 403)
    if slug != "PUBLIC-INVOICE-METADATA":
        assert not _trace_proof_assets(prop, fixture, [denied])
    if slug == "PUBLIC-INVOICE-METADATA":
        missing_field = _action(identity, method, path, asset, {"id": str(asset)}, 200)
        assert not _trace_proof_assets(prop, fixture, [missing_field])
    else:
        wrong_identity = next(
            UUID(account["id"])
            for account in fixture["accounts"]
            if account["id"] != str(identity)
            and not _relation_met(fixture, prop, account, prop.expectation.object_relation, asset)
        )
        invalid = _action(wrong_identity, method, path, asset, body, 200)
        assert not _trace_proof_assets(prop, fixture, [invalid])
