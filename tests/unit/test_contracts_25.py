"""Cross-contract checks for typed oracle, graph, action, and provenance models."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.saas import SaasRangeCompiler, fixture_for_seed
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.attack_graph import (
    AttackGraphEdge,
    AttackGraphManifest,
    AttackGraphNode,
)
from offsecgym.schemas.common import derived_id
from offsecgym.schemas.domain import (
    AgentContext,
    AuthorizationExpectation,
    CandidateFinding,
    EntityRef,
    EvidenceRef,
    FieldExposureExpectation,
    RangeControllerMetadata,
    RangeIdentity,
    RangeInstanceStatus,
    SecurityExpectation,
    StateTransitionExpectation,
    ValidationResult,
    WorldFact,
    agent_visible_context,
)
from offsecgym.schemas.events import ActionRequested, parse_event
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.ground_truth import (
    AnonymousRequestRequirement,
    GroundTruthManifest,
    GroundTruthObject,
    GroundTruthProperty,
    GroundTruthSubject,
    RootCause,
)
from offsecgym.schemas.specs import RangeSpec

NAMESPACE = UUID("b5de8263-9dc0-425e-83c1-93007a4d6250")


def stable_id(slug: str) -> UUID:
    return derived_id(NAMESPACE, "tenant_boundary_v1", "42", slug)


def sample_oracle_and_graph(active: bool) -> tuple[GroundTruthManifest, AttackGraphManifest]:
    build_id = uuid4()
    pair_id = uuid4()
    property_id = stable_id("PUBLIC-INVOICE-METADATA")
    root_cause_id = stable_id("public-preview-serializes-private-field")
    oracle = GroundTruthManifest(
        scenario_id="tenant_boundary_v1",
        build_id=build_id,
        pair_id=pair_id,
        seed=42,
        variant="vulnerable" if active else "patched",
        root_causes=(
            RootCause(
                root_cause_id=root_cause_id,
                slug="public-preview-serializes-private-field",
                description="Public preview includes billing email.",
            ),
        ),
        properties=(
            GroundTruthProperty(
                property_id=property_id,
                slug="PUBLIC-INVOICE-METADATA",
                family="information_exposure",
                component="billing",
                subject=GroundTruthSubject(role="anonymous"),
                object=GroundTruthObject(object_id=uuid4(), resource_type="invoice"),
                expectation=FieldExposureExpectation(
                    action="GET /api/public/invoices/{id}/preview",
                    resource_type="invoice",
                    forbidden_fields=("billing_email",),
                ),
                vulnerable_effect="billing_email_returned",
                root_cause_id=root_cause_id,
                proof_requirements=(AnonymousRequestRequirement(),),
                active=active,
            ),
        ),
    )
    start, tested, exploited = (stable_id(slug) for slug in ("start", "tested", "exploited"))
    graph = AttackGraphManifest(
        scenario_id=oracle.scenario_id,
        build_id=oracle.build_id,
        pair_id=oracle.pair_id,
        seed=oracle.seed,
        variant=oracle.variant,
        nodes=(
            AttackGraphNode(node_id=start, slug="start"),
            AttackGraphNode(node_id=tested, slug="tested"),
            AttackGraphNode(node_id=exploited, slug="exploited"),
        ),
        edges=(
            AttackGraphEdge(
                edge_id=stable_id("boundary-edge"),
                from_node=start,
                to_node=tested,
                action_family="http_request",
                property_id=property_id,
                kind="boundary_test",
                active=True,
                expected_outcome="observed" if active else "blocked",
                evidence_requirements=("public_preview_response",),
            ),
            AttackGraphEdge(
                edge_id=stable_id("violation-edge"),
                from_node=tested,
                to_node=exploited,
                prerequisites=(tested,),
                action_family="http_request",
                property_id=property_id,
                kind="violation",
                active=active,
                expected_outcome="succeeded" if active else "blocked",
                evidence_requirements=("billing_email_in_response",),
            ),
        ),
    )
    return oracle, graph


@pytest.mark.parametrize("active", [True, False])
def test_oracle_graph_finding_and_validation_share_canonical_id_type(active: bool) -> None:
    oracle, graph = sample_oracle_and_graph(active)
    oracle = GroundTruthManifest.model_validate_json(oracle.model_dump_json())
    graph = AttackGraphManifest.model_validate_json(graph.model_dump_json())
    graph.validate_against_ground_truth(oracle)
    prop = oracle.properties[0]
    assert isinstance(prop.property_id, UUID)
    assert isinstance(prop.root_cause_id, UUID)
    assert prop.property_id == graph.edges[0].property_id
    assert graph.edges[0].active
    assert graph.edges[1].active is active
    finding = CandidateFinding(
        finding_id=uuid4(),
        run_id=uuid4(),
        range_instance_id=uuid4(),
        range_generation=0,
        claim="Anonymous preview exposed a private field.",
        family=prop.family,
        asset_id=prop.object.object_id,
        security_property=prop.expectation,
        evidence=(EvidenceRef(evidence_id=uuid4(), action_id=uuid4(), description="response"),),
    )
    assert prop.slug not in finding.model_dump_json()
    assert "property_id" not in finding.model_dump()
    assert "root_cause_id" not in finding.model_dump()
    verdict = ValidationResult(
        finding_id=finding.finding_id,
        status="validated" if active else "rejected",
        matched_property_id=prop.property_id,
        matched_root_cause_id=prop.root_cause_id,
    )
    assert verdict.matched_property_id == graph.edges[1].property_id
    assert oracle.active_property_ids == ((prop.property_id,) if active else ())


def test_ground_truth_and_graph_reject_dangling_references() -> None:
    oracle, graph = sample_oracle_and_graph(True)
    random_property = oracle.model_dump(mode="python")
    random_property["properties"][0]["property_id"] = uuid4()
    with pytest.raises(ValidationError, match="UUIDv5"):
        GroundTruthManifest.model_validate(random_property)
    bad_oracle = oracle.model_dump(mode="python")
    bad_oracle["properties"][0]["root_cause_id"] = uuid4()
    with pytest.raises(ValidationError, match="unknown root cause"):
        GroundTruthManifest.model_validate(bad_oracle)
    bad_graph = graph.model_dump(mode="python")
    bad_graph["edges"][0]["from_node"] = uuid4()
    with pytest.raises(ValidationError, match="unknown node"):
        AttackGraphManifest.model_validate(bad_graph)
    changed = graph.model_dump(mode="python")
    changed["edges"][0]["property_id"] = uuid4()
    with pytest.raises(ValueError, match="unknown property"):
        AttackGraphManifest.model_validate(changed).validate_against_ground_truth(oracle)


def test_security_expectation_discriminator_covers_three_families() -> None:
    adapter = TypeAdapter(SecurityExpectation)
    auth = AuthorizationExpectation(
        subject_role="member",
        action="GET /api/documents/{id}",
        resource_type="document",
        object_relation="foreign_workspace",
        expected="deny",
    )
    transition = StateTransitionExpectation(
        subject_role="member",
        action="POST /api/invoices/{id}/refund",
        resource_type="invoice",
        object_relation="own_workspace",
        from_state="paid",
        to_state="refunded",
        expected="deny",
        allowed_roles=("workspace_admin", "platform_admin"),
    )
    field = FieldExposureExpectation(
        resource_type="invoice",
        action="GET /api/public/invoices/{id}/preview",
        forbidden_fields=("billing_email",),
    )
    for expected in (auth, transition, field):
        parsed = adapter.validate_json(expected.model_dump_json())
        assert type(parsed) is type(expected)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "field_exposure", "expected": "deny"})


def test_recursive_action_json_is_bounded_and_object_rooted() -> None:
    args = dict(run_id=uuid4(), kind="http_request", destination="saas", method="POST", path="/x")
    body = {"amount": 100, "active": True, "permissions": ["read"], "filter": {"state": None}}
    action = ActionRequest(**args, json_body=body)
    assert action.json_body == body
    assert ActionRequest.model_validate_json(action.model_dump_json()).json_body == body
    for bad in ([1, 2], {"value": float("nan")}, {"blob": "x" * 4096}):
        with pytest.raises(ValidationError):
            ActionRequest(**args, json_body=bad)
    with pytest.raises(ValidationError, match="depth"):
        ActionRequest(**args, json_body={"nested": [[[[[[[[[1]]]]]]]]]})
    with pytest.raises(ValidationError, match="elements"):
        ActionRequest(**args, json_body={str(index): index for index in range(257)})


def test_action_requested_v2_provenance_and_legacy_v1_readability() -> None:
    base = dict(
        run_id=uuid4(),
        actor="gateway",
        action_id=uuid4(),
        action_type="http_request",
        destination="saas",
    )
    legacy = ActionRequested(**base, schema_version="1")
    assert parse_event(legacy.model_dump(mode="json")) == legacy
    assert legacy.range_instance_id is None
    instance_id, artifact_id = uuid4(), uuid4()
    current = ActionRequested(
        **base,
        range_instance_id=instance_id,
        range_generation=3,
        request_artifact_id=artifact_id,
    )
    assert parse_event(current.model_dump(mode="json")) == current
    assert current.schema_version == "2"
    with pytest.raises(ValidationError, match="requires instance"):
        ActionRequested(**base)
    with pytest.raises(ValidationError, match="cannot include v2 provenance"):
        ActionRequested(**base, schema_version="1", range_instance_id=instance_id)


def test_evidence_and_request_artifact_bind_instance_generation() -> None:
    run_id, instance_id, action_id, artifact_id = (uuid4() for _ in range(4))
    request = RequestArtifact(
        request_artifact_id=artifact_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=2,
        destination="saas",
        method="POST",
        path="/api/invoices/redacted/refund",
        json_body={"reason": "duplicate"},
    )
    body = b'{"status":"paid"}'
    evidence = Evidence(
        evidence_id=uuid4(),
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=2,
        request_artifact_id=artifact_id,
        http_status=200,
        body_b64=base64.b64encode(body).decode(),
        response_sha256=hashlib.sha256(body).hexdigest(),
    )
    assert evidence.range_generation == request.range_generation
    with pytest.raises(ValidationError, match="hash mismatch"):
        Evidence.model_validate({**evidence.model_dump(), "response_sha256": "0" * 64})


def test_world_fact_provenance_contradiction_and_metadata_projection() -> None:
    fact_id = uuid4()
    fact = WorldFact(
        fact_id=fact_id,
        run_id=uuid4(),
        subject=EntityRef(entity_id=uuid4(), entity_type="endpoint"),
        predicate="requires_authentication",
        object_value=True,
        source_event_ids=(uuid4(),),
        confidence=0.75,
        contradicts_fact_ids=(uuid4(),),
    )
    assert isinstance(fact.object_value, bool)
    with pytest.raises(ValidationError, match="source provenance"):
        WorldFact.model_validate({**fact.model_dump(), "source_event_ids": ()})
    with pytest.raises(ValidationError, match="itself"):
        WorldFact.model_validate({**fact.model_dump(), "supersedes_fact_id": fact_id})
    identity = RangeIdentity(
        identity_id=uuid4(), username="member_0", role="member", workspace_id=uuid4()
    )
    metadata = RangeControllerMetadata(
        instance_id=uuid4(),
        build_id=uuid4(),
        generation=1,
        project_name="offsecgym_test",
        spec_sha256="0" * 64,
        seed=42,
        state="healthy",
        family="saas",
        security_variant="vulnerable",
        identities=(identity,),
    )
    with pytest.raises(ValidationError, match="saas range requires"):
        RangeControllerMetadata.model_validate(
            {**metadata.model_dump(mode="python"), "security_variant": None}
        )
    with pytest.raises(ValidationError, match="hello range has no"):
        RangeControllerMetadata.model_validate(
            {**metadata.model_dump(mode="python"), "family": "hello"}
        )
    public = agent_visible_context(metadata)
    assert public.identity_ids == public.known_roles == ()
    assert "username" not in public.model_dump_json()
    assert "workspace_id" not in public.model_dump_json()
    assert AgentContext(run_id=uuid4(), objective="test", range=public).range == public
    with pytest.raises(ValidationError):
        AgentContext(run_id=uuid4(), objective="test", range=metadata)
    with pytest.raises(ValidationError):
        RangeInstanceStatus(
            instance_id=metadata.instance_id,
            build_id=metadata.build_id,
            generation=0,
            state="built",
            checked_at=datetime.now(UTC),
        )


@pytest.mark.parametrize("seed", [0, 1, 2, 42, 1000, 2147483647])
def test_generated_saas_oracle_graph_and_fixture_contracts(tmp_path: Path, seed: int) -> None:
    raw = yaml.safe_load(
        (Path(__file__).parents[2] / "examples" / "saas-range.yaml").read_text(encoding="utf-8")
    )
    raw["seed"] = seed
    base = RangeSpec.model_validate(raw)
    state = StateStore(tmp_path)
    compiler = SaasRangeCompiler(state)
    vulnerable = compiler.build(base)
    patched = compiler.build(base.model_copy(update={"patched": True}))
    assert vulnerable.build_id != patched.build_id
    assert vulnerable.pair_id == patched.pair_id
    fixture_bytes = (state.build_dir(vulnerable.build_id) / "fixture.json").read_bytes()
    assert fixture_bytes == (state.build_dir(patched.build_id) / "fixture.json").read_bytes()
    fixture = json.loads(fixture_bytes)
    assert fixture_for_seed(seed) == fixture
    assert fixture_for_seed(seed + 1)["workspaces"][0]["id"] != fixture["workspaces"][0]["id"]

    workspaces = {item["id"] for item in fixture["workspaces"]}
    accounts = {item["id"]: item for item in fixture["accounts"]}
    documents = {item["id"]: item for item in fixture["documents"]}
    invoices = {item["id"]: item for item in fixture["invoices"]}
    tickets = {item["id"]: item for item in fixture["tickets"]}
    all_ids = [
        item["id"]
        for kind in ("workspaces", "accounts", "documents", "invoices", "tickets")
        for item in fixture[kind]
    ]
    assert len(all_ids) == len(set(all_ids))
    assert all(UUID(item).version == 5 for item in all_ids)
    for account in accounts.values():
        assert account["workspace_id"] is None or account["workspace_id"] in workspaces
    for objects in (documents, invoices, tickets):
        assert all(item["workspace_id"] in workspaces for item in objects.values())
    for document in documents.values():
        assert document["reference_document_id"] is None or (
            document["reference_document_id"] in documents
        )
        assert document["reference_ticket_id"] is None or (
            document["reference_ticket_id"] in tickets
        )
        assert any(invoice_id in document["body"] for invoice_id in invoices)
    for ticket in tickets.values():
        assert any(invoice_id in ticket["body"] for invoice_id in invoices)

    oracle_pairs: list[tuple[GroundTruthManifest, AttackGraphManifest]] = []
    for build, expected_active in ((vulnerable, True), (patched, False)):
        hidden = state.root / "oracles" / build.build_id.hex
        oracle = GroundTruthManifest.model_validate_json(
            (hidden / "ground_truth.json").read_text(encoding="utf-8")
        )
        graph = AttackGraphManifest.model_validate_json(
            (hidden / "attack_graph.json").read_text(encoding="utf-8")
        )
        graph.validate_against_ground_truth(oracle)
        oracle_pairs.append((oracle, graph))
        assert oracle.build_id == graph.build_id == build.build_id
        assert oracle.pair_id == graph.pair_id == build.pair_id
        assert len(oracle.properties) == 5
        root_ids = {cause.root_cause_id for cause in oracle.root_causes}
        property_ids = {prop.property_id for prop in oracle.properties}
        assert all(prop.active is expected_active for prop in oracle.properties)
        assert {edge.property_id for edge in graph.edges if edge.property_id} == property_ids
        for prop in oracle.properties:
            assert prop.root_cause_id in root_ids
            assert prop.subject.identity_id is None or str(prop.subject.identity_id) in accounts
            object_id = str(prop.object.object_id)
            objects = {"document": documents, "invoice": invoices, "ticket": tickets}[
                prop.object.resource_type
            ]
            assert object_id in objects
            assert str(prop.object.workspace_id) == objects[object_id]["workspace_id"]
            if isinstance(prop.expectation, (AuthorizationExpectation, StateTransitionExpectation)):
                relation = prop.expectation.object_relation
                subject_workspace = prop.subject.workspace_id
                if relation == "foreign_workspace":
                    assert subject_workspace != prop.object.workspace_id
                elif relation == "own_workspace":
                    assert subject_workspace == prop.object.workspace_id
            finding = CandidateFinding(
                finding_id=uuid4(),
                run_id=uuid4(),
                range_instance_id=uuid4(),
                range_generation=0,
                claim=f"Observed {prop.family} behavior",
                family=prop.family,
                asset_id=prop.object.object_id,
                security_property=prop.expectation,
                evidence=(
                    EvidenceRef(evidence_id=uuid4(), action_id=uuid4(), description="proof"),
                ),
            )
            assert "property_id" not in finding.model_dump()
            assert "root_cause_id" not in finding.model_dump()
            result = ValidationResult(
                finding_id=finding.finding_id,
                status="validated" if expected_active else "rejected",
                matched_property_id=prop.property_id,
                matched_root_cause_id=prop.root_cause_id,
            )
            assert result.matched_property_id == prop.property_id
            boundary = [
                edge
                for edge in graph.edges
                if edge.property_id == prop.property_id and edge.kind == "boundary_test"
            ]
            violation = [
                edge
                for edge in graph.edges
                if edge.property_id == prop.property_id and edge.kind == "violation"
            ]
            assert boundary and violation
            assert all(edge.active for edge in boundary)
            assert all(edge.active is expected_active for edge in violation)
    assert {item.property_id for item in oracle_pairs[0][0].properties} == {
        item.property_id for item in oracle_pairs[1][0].properties
    }
    assert {item.root_cause_id for item in oracle_pairs[0][0].root_causes} == {
        item.root_cause_id for item in oracle_pairs[1][0].root_causes
    }
    assert {item.node_id for item in oracle_pairs[0][1].nodes} == {
        item.node_id for item in oracle_pairs[1][1].nodes
    }
    assert {item.edge_id for item in oracle_pairs[0][1].edges} == {
        item.edge_id for item in oracle_pairs[1][1].edges
    }
