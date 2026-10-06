"""Route-bound Range B entity extraction with exact response provenance."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from offsecgym.runtime.enterprise import FAMILY, fixture_for_seed
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import EntityRef
from offsecgym.worldview.extract import ResponseFactExtractor


def extract(path: str, identity: UUID, body: dict[str, object], *, truncated=False):
    text = json.dumps(body, sort_keys=True)
    action = ActionRequest(
        run_id=uuid4(),
        kind="http_request",
        destination=FAMILY,
        method="GET",
        path=path,
        identity_id=identity,
    )
    result = ActionResult(
        action_id=action.action_id,
        status="completed",
        duration_ms=1,
        evidence_id=uuid4(),
        http_status=200,
        body_text=text,
        response_sha256=hashlib.sha256(text.encode()).hexdigest(),
        truncated=truncated,
    )
    return ResponseFactExtractor().extract(action, result)


def test_enterprise_entities_and_relationships_from_complete_reads() -> None:
    fixture, anchors = fixture_for_seed(42)
    actor = UUID(anchors["b2_actor"])
    user = next(item for item in fixture["users"] if item["id"] == str(actor))
    roles = [item for item in fixture["role_assignments"] if item["user_id"] == str(actor)]
    me = extract("/api/me", actor, {**user, "roles": roles})
    assert any(f.subject.entity_type == "identity" and f.predicate == "username" for f in me)
    assert any(f.subject.entity_type == "role_assignment" and f.predicate == "active" for f in me)
    assert any(
        f.predicate == "has_role_in_project" and isinstance(f.object_value, EntityRef) for f in me
    )
    change = next(item for item in fixture["changes"] if item["id"] == anchors["b2_change"])
    facts = extract(f"/api/changes/{change['id']}", actor, change)
    assert {(f.subject.entity_type, f.predicate) for f in facts} >= {
        ("change_request", "status"),
        ("change_request", "requested_by"),
        ("change_request", "targets_environment"),
        ("change_request", "belongs_to_project"),
    }
    env = next(item for item in fixture["environments"] if item["id"] == anchors["b3_environment"])
    assert any(
        f.predicate == "revision" and f.object_value == env["revision"]
        for f in extract(f"/api/environments/{env['id']}", actor, env)
    )
    job = fixture["jobs"][0]
    assert any(
        f.predicate == "executes_change" and isinstance(f.object_value, EntityRef)
        for f in extract(f"/api/jobs/{job['id']}", actor, job)
    )
    job_facts = extract(f"/api/jobs/{job['id']}", actor, job)
    assert any(
        f.predicate == "queued_change_revision" and f.object_value == job["queued_change_revision"]
        for f in job_facts
    )
    assert any(f.predicate == "queued_authorized" and f.object_value is True for f in job_facts)


def test_extractor_rejects_truncation_and_route_object_mismatch() -> None:
    fixture, anchors = fixture_for_seed(42)
    actor = UUID(anchors["b1_actor"])
    item = next(x for x in fixture["access_requests"] if x["id"] == anchors["b1_request"])
    path = f"/api/access-requests/{item['id']}"
    assert extract(path, actor, item)
    assert not extract(path, actor, item, truncated=True)
    assert not extract(f"/api/access-requests/{uuid4()}", actor, item)
    assert not extract("/api/me", uuid4(), {"id": str(actor), "roles": []})
