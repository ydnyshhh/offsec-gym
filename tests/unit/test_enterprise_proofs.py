"""Root-specific ordered proof contracts, including important negative cases."""

from __future__ import annotations

import base64
import hashlib
import json
from uuid import UUID, uuid4

from offsecgym.runtime.enterprise import FAMILY, fixture_for_seed, oracle_for_fixture
from offsecgym.schemas.domain import CandidateFinding, EvidenceRef
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.validation.deterministic import ProofAction
from offsecgym.validation.enterprise import validate_enterprise_proof

RUN = uuid4()
INSTANCE = uuid4()


def proof(
    sequence: int,
    identity: str,
    method: str,
    path: str,
    body: dict[str, object],
    status: int = 200,
    truncated: bool = False,
) -> ProofAction:
    action_id, evidence_id, request_id = uuid4(), uuid4(), uuid4()
    raw = json.dumps(body, sort_keys=True).encode()
    request = RequestArtifact(
        request_artifact_id=request_id,
        run_id=RUN,
        action_id=action_id,
        range_instance_id=INSTANCE,
        range_generation=0,
        identity_id=UUID(identity),
        destination=FAMILY,
        method=method,
        path=path,
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=RUN,
        action_id=action_id,
        range_instance_id=INSTANCE,
        range_generation=0,
        request_artifact_id=request_id,
        identity_id=UUID(identity),
        http_status=status,
        body_b64=base64.b64encode(raw).decode(),
        response_sha256=hashlib.sha256(raw).hexdigest(),
        truncated=truncated,
    )
    return ProofAction(request, evidence, sequence)


def context():
    fixture, anchors = fixture_for_seed(42)
    oracle = oracle_for_fixture(fixture, anchors, frozenset(), uuid4(), uuid4())
    properties = {p.slug: p for p in oracle.properties}
    return fixture, anchors, properties


def finding(prop):
    return CandidateFinding(
        finding_id=uuid4(),
        run_id=RUN,
        range_instance_id=INSTANCE,
        range_generation=0,
        claim="Observed transition",
        family=prop.family,
        asset_id=prop.object.object_id,
        security_property=prop.expectation,
        evidence=(
            EvidenceRef(evidence_id=uuid4(), action_id=uuid4(), description="test evidence"),
        ),
    )


def test_b1_requires_same_requester_approver_and_ordered_after() -> None:
    fixture, anchors, properties = context()
    prop = properties["B1-SOD"]
    actor, asset, project = anchors["b1_actor"], anchors["b1_request"], anchors["b1_project"]
    path = f"/api/access-requests/{asset}"
    common = {
        "id": asset,
        "project_id": project,
        "requester_id": actor,
        "target_user_id": actor,
        "requested_role": "operator",
    }
    actions = [
        proof(2, actor, "GET", path, {**common, "status": "PENDING"}),
        proof(
            4,
            actor,
            "POST",
            path + "/approve",
            {**common, "status": "APPLIED", "approver_id": actor},
        ),
        proof(6, actor, "GET", path, {**common, "status": "APPLIED", "approver_id": actor}),
    ]
    claim = finding(prop)
    assert validate_enterprise_proof(claim, prop, actions, fixture) is None
    assert validate_enterprise_proof(claim, prop, actions[:2], fixture) == "state_after_missing"
    swapped = [
        actions[0],
        actions[1],
        proof(3, actor, "GET", path, {**common, "status": "APPLIED", "approver_id": actor}),
    ]
    assert validate_enterprise_proof(claim, prop, swapped, fixture) == "ordering_invalid"
    other = {**common, "requester_id": str(uuid4())}
    assert (
        validate_enterprise_proof(
            claim,
            prop,
            [proof(2, actor, "GET", path, {**other, "status": "PENDING"}), *actions[1:]],
            fixture,
        )
        == "ordering_invalid"
    )
    assert (
        validate_enterprise_proof(
            claim,
            prop,
            [
                actions[0],
                proof(
                    4,
                    actor,
                    "POST",
                    path + "/approve",
                    {**common, "status": "APPLIED", "approver_id": actor},
                    truncated=True,
                ),
                actions[2],
            ],
            fixture,
        )
        == "transition_failed"
    )


def test_b2_requires_role_before_revocation_after_and_same_actor() -> None:
    fixture, anchors, properties = context()
    prop = properties["B2-REVOKED-ROLE"]
    actor, admin, asset, project = (
        anchors[k] for k in ("b2_actor", "b2_admin", "b2_change", "b2_project")
    )
    detail = f"/api/changes/{asset}"
    role_id = str(uuid4())

    def me(active):
        return {
            "id": actor,
            "roles": [
                {"id": role_id, "project_id": project, "role": "change_approver", "active": active}
            ],
        }

    actions = [
        proof(2, actor, "GET", "/api/me", me(True)),
        proof(
            4,
            admin,
            "POST",
            f"/api/projects/{project}/roles/{actor}/revoke",
            {
                "id": role_id,
                "user_id": actor,
                "project_id": project,
                "role": "change_approver",
                "active": False,
            },
        ),
        proof(6, actor, "GET", "/api/me", me(False)),
        proof(
            8,
            actor,
            "GET",
            detail,
            {"id": asset, "project_id": project, "status": "PENDING_APPROVAL"},
        ),
        proof(
            10,
            actor,
            "POST",
            detail + "/approve",
            {"id": asset, "project_id": project, "status": "APPROVED", "approver_id": actor},
        ),
        proof(12, actor, "GET", detail, {"id": asset, "project_id": project, "status": "APPROVED"}),
    ]
    claim = finding(prop)
    assert validate_enterprise_proof(claim, prop, actions, fixture) is None
    assert (
        validate_enterprise_proof(claim, prop, actions[:1] + actions[2:], fixture)
        == "transition_failed"
    )
    assert (
        validate_enterprise_proof(claim, prop, actions[:2] + actions[3:], fixture)
        == "state_before_missing"
    )
    assert validate_enterprise_proof(claim, prop, actions[:-1], fixture) == "state_after_missing"
    wrong_scope = proof(
        4,
        admin,
        "POST",
        f"/api/projects/{uuid4()}/roles/{actor}/revoke",
        {
            "id": role_id,
            "user_id": actor,
            "project_id": project,
            "role": "change_approver",
            "active": False,
        },
    )
    assert (
        validate_enterprise_proof(claim, prop, [actions[0], wrong_scope, *actions[2:]], fixture)
        == "transition_failed"
    )


def test_b3_requires_queue_cancel_worker_completion_and_environment_revision() -> None:
    fixture, anchors, properties = context()
    prop = properties["B3-CANCELLED-JOB"]
    actor, change, env, project = (
        anchors[k] for k in ("b3_actor", "b3_change", "b3_environment", "b1_project")
    )
    job = str(uuid4())
    detail = f"/api/changes/{change}"
    env_path = f"/api/environments/{env}"
    env_body = {"id": env, "project_id": project}
    job_body = {
        "id": job,
        "change_id": change,
        "environment_id": env,
        "initiator_id": actor,
        "queued_tick": 2,
    }
    events = [
        {"sequence": 20, "action": "job.queued", "object_id": job, "project_id": project},
        {"sequence": 21, "action": "change.cancel", "object_id": change, "project_id": project},
        {"sequence": 22, "action": "job.started", "object_id": job, "project_id": project},
        {
            "sequence": 23,
            "action": "environment.revision_changed",
            "object_id": env,
            "project_id": project,
        },
        {"sequence": 24, "action": "job.completed", "object_id": job, "project_id": project},
    ]
    actions = [
        proof(2, actor, "GET", env_path, {**env_body, "revision": 10}),
        proof(
            4,
            actor,
            "POST",
            detail + "/deploy",
            {"id": change, "project_id": project, "status": "QUEUED"},
            status=202,
        ),
        proof(6, actor, "GET", f"/api/jobs/{job}", {**job_body, "status": "QUEUED"}),
        proof(
            8,
            actor,
            "POST",
            detail + "/cancel",
            {"id": change, "project_id": project, "status": "CANCELLED"},
        ),
        proof(
            10,
            actor,
            "GET",
            f"/api/jobs/{job}",
            {**job_body, "status": "SUCCEEDED", "finished_tick": 7},
        ),
        proof(12, actor, "GET", env_path, {**env_body, "revision": 11}),
        proof(14, actor, "GET", "/api/audit?limit=20&offset=20", {"items": events}),
    ]
    claim = finding(prop)
    assert validate_enterprise_proof(claim, prop, actions, fixture) is None
    assert (
        validate_enterprise_proof(claim, prop, actions[:4] + actions[5:], fixture)
        == "execution_not_observed"
    )
    assert (
        validate_enterprise_proof(claim, prop, actions[:5] + actions[6:], fixture)
        == "ordering_invalid"
    )
    assert validate_enterprise_proof(claim, prop, actions[:-1], fixture) == "ordering_invalid"
    after_execution = [*events]
    after_execution[1] = {**after_execution[1], "sequence": 25}
    assert (
        validate_enterprise_proof(
            claim,
            prop,
            [
                *actions[:-1],
                proof(
                    14, actor, "GET", "/api/audit?limit=20&offset=20", {"items": after_execution}
                ),
            ],
            fixture,
        )
        == "ordering_invalid"
    )
