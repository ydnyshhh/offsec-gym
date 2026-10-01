"""M5.2 response extraction, exact-ID state, ontology, and provider routing."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError
from test_milestone_3_runner import MemoryEvents

from offsecgym.providers.openrouter import OpenRouterResponsesProvider
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import (
    AuthorizationExpectation,
    CandidateFinding,
    EntityRef,
    EvidenceRef,
    WorldFact,
)
from offsecgym.schemas.events import ActionCompleted, ActionRequested, WorldFactSubmitted
from offsecgym.schemas.specs import ExperimentSpec, ModelSpec
from offsecgym.solver.monolithic import AuthorizationFindingArgs, TransitionFindingArgs
from offsecgym.validation.ontology import normalize_legacy_m5v1
from offsecgym.worldview import EventWorldState, WorldContextBuilder
from offsecgym.worldview.extract import ResponseFactExtractor
from offsecgym.worldview.ledger import EntityLedger


def _result(request: ActionRequest, body: object, *, status: int = 200) -> ActionResult:
    body_text = json.dumps(body)
    return ActionResult(
        action_id=request.action_id,
        status="completed",
        evidence_id=uuid4(),
        duration_ms=1,
        http_status=status,
        body_text=body_text,
        response_sha256=hashlib.sha256(body_text.encode()).hexdigest(),
    )


def test_extracts_identity_and_objects_only_from_matching_complete_responses() -> None:
    extractor = ResponseFactExtractor()
    identity, workspace, invoice, document = (uuid4() for _ in range(4))
    request = ActionRequest(
        run_id=uuid4(),
        kind="http_request",
        destination="saas",
        method="GET",
        path="/api/me",
        identity_id=identity,
    )
    body = {
        "id": str(identity),
        "role": "workspace_admin",
        "workspace_id": str(workspace),
        "username": "alice",
    }
    facts = extractor.extract(request, _result(request, body))
    assert {(fact.predicate, fact.object_value) for fact in facts} == {
        ("role", "workspace_admin"),
        ("member_of", EntityRef(entity_id=workspace, entity_type="workspace")),
        ("username", "alice"),
    }
    assert extractor.extract(request, _result(request, {**body, "id": str(uuid4())})) == ()
    assert extractor.extract(request, _result(request, body, status=403)) == ()
    assert (
        extractor.extract(
            request, _result(request, body).model_copy(update={"response_sha256": "0" * 64})
        )
        == ()
    )
    assert (
        extractor.extract(request, _result(request, body).model_copy(update={"truncated": True}))
        == ()
    )

    listing = request.model_copy(update={"path": f"/api/workspaces/{workspace}/invoices"})
    listed = extractor.extract(
        listing,
        _result(
            listing,
            {"items": [{"id": str(invoice), "workspace_id": str(workspace)}]},
        ),
    )
    assert len(listed) == 2
    assert listed[0].subject.entity_id == workspace
    assert listed[0].object_value == EntityRef(entity_id=invoice, entity_type="invoice")
    assert listed[1].subject.entity_id == invoice
    assert listed[1].object_value == EntityRef(entity_id=workspace, entity_type="workspace")

    detail = request.model_copy(update={"path": f"/api/documents/{document}"})
    detailed = extractor.extract(
        detail,
        _result(
            detail,
            {
                "id": str(document),
                "workspace_id": str(workspace),
                "title": "private document",
                "body": "large document content is not promoted to state",
            },
        ),
    )
    assert {fact.predicate for fact in detailed} == {"workspace", "title", "body_excerpt"}
    assert (
        extractor.extract(
            detail, _result(detail, {"id": str(uuid4()), "workspace_id": str(workspace)})
        )
        == ()
    )


def test_invoice_ticket_preview_and_refund_extract_only_present_fields() -> None:
    extractor = ResponseFactExtractor()
    invoice, ticket, workspace = uuid4(), uuid4(), uuid4()
    base = ActionRequest(
        run_id=uuid4(),
        kind="http_request",
        destination="saas",
        method="GET",
        path=f"/api/invoices/{invoice}",
        identity_id=uuid4(),
    )
    detail = extractor.extract(
        base,
        _result(
            base,
            {
                "id": str(invoice),
                "workspace_id": str(workspace),
                "status": "paid",
                "amount_cents": 200,
                "billing_email": "billing@example.test",
            },
        ),
    )
    assert {fact.predicate for fact in detail} == {
        "workspace",
        "status",
        "amount_cents",
        "billing_email",
    }
    ticket_request = base.model_copy(update={"path": f"/api/support/tickets/{ticket}"})
    ticket_facts = extractor.extract(
        ticket_request,
        _result(
            ticket_request,
            {"id": str(ticket), "workspace_id": str(workspace), "title": "Help"},
        ),
    )
    assert {fact.predicate for fact in ticket_facts} == {"workspace", "title"}
    preview = base.model_copy(update={"path": f"/api/public/invoices/{invoice}/preview"})
    preview_facts = extractor.extract(
        preview,
        _result(preview, {"id": str(invoice), "billing_email": "billing@example.test"}),
    )
    assert [fact.predicate for fact in preview_facts] == ["public_billing_email"]
    refund = base.model_copy(update={"method": "POST", "path": f"/api/invoices/{invoice}/refund"})
    assert (
        extractor.extract(refund, _result(refund, {"id": str(invoice), "status": "refunded"}))[
            0
        ].object_value
        == "refunded"
    )


@pytest.mark.asyncio
async def test_controller_observation_supersedes_dynamic_status_and_renders_exact_ids() -> None:
    events = MemoryEvents()
    state = EventWorldState(events)
    run_id, invoice = uuid4(), uuid4()
    prior: WorldFact | None = None
    for value in ("paid", "refunded"):
        request = ActionRequest(
            run_id=run_id,
            kind="http_request",
            destination="saas",
            method="GET",
            path=f"/api/invoices/{invoice}",
        )
        result = _result(request, {"id": str(invoice), "status": value})
        await events.append(
            ActionRequested(
                run_id=run_id,
                actor="gateway",
                action_id=request.action_id,
                action_type="http_request",
                destination="saas",
                method="GET",
                path_sha256=hashlib.sha256(request.path.encode()).hexdigest(),
                range_instance_id=uuid4(),
                range_generation=0,
                request_artifact_id=uuid4(),
            )
        )
        completed = await events.append(
            ActionCompleted(
                run_id=run_id,
                actor="gateway",
                action_id=request.action_id,
                evidence_id=result.evidence_id,
                duration_ms=1,
                http_status=200,
                response_sha256=result.response_sha256,
            )
        )
        (fact,) = await state.record_response(request, result)
        prior = fact if value == "paid" else prior
    current = list(await state.query(run_id))
    assert len(current) == 1 and current[0].object_value == "refunded"
    assert prior is not None
    all_facts = {fact.fact_id: fact for fact in await state.query(run_id, include_superseded=True)}
    assert all_facts[prior.fact_id].status == "superseded"
    assert all_facts[prior.fact_id].superseded_by_fact_id == current[0].fact_id
    assert all(
        event.actor == "controller"
        for event in events.items
        if isinstance(event, WorldFactSubmitted)
    )
    context = await WorldContextBuilder(state, events).build(run_id, str(invoice))
    assert str(invoice) in context.text and 'status="refunded"' in context.text
    assert f"evidence={result.evidence_id}" in context.text
    assert current[0].fact_id in context.fact_ids
    ledger = EntityLedger(current).as_dict()
    assert ledger[0]["id"] == str(invoice)
    assert str(result.evidence_id) in ledger[0]["evidence_ids"]
    model_claim = WorldFact(
        fact_id=uuid4(),
        run_id=run_id,
        kind="observation",
        subject=EntityRef(entity_id=invoice, entity_type="invoice"),
        predicate="status",
        object_value="issued",
        source_event_ids=(completed.event_id,),
        source_action_ids=(request.action_id,),
        evidence_ids=(result.evidence_id,),
        confidence=0.8,
    )
    await state.submit_fact(model_claim)
    adjudicated = await state.adjudicate_fact(run_id, model_claim.fact_id)
    assert adjudicated.status == "contradicted"
    assert (await state.query(run_id, predicate="status"))[0].status == "observed"


@pytest.mark.asyncio
async def test_controller_fact_order_follows_action_completion_not_persistence() -> None:
    events = MemoryEvents()
    state = EventWorldState(events)
    run_id, invoice = uuid4(), uuid4()
    responses = []
    for value in ("paid", "refunded"):
        request = ActionRequest(
            run_id=run_id,
            kind="http_request",
            destination="saas",
            method="GET",
            path=f"/api/invoices/{invoice}",
        )
        result = _result(request, {"id": str(invoice), "status": value})
        await events.append(
            ActionRequested(
                run_id=run_id,
                actor="gateway",
                action_id=request.action_id,
                action_type="http_request",
                destination="saas",
                method="GET",
                path_sha256=hashlib.sha256(request.path.encode()).hexdigest(),
                range_instance_id=uuid4(),
                range_generation=0,
                request_artifact_id=uuid4(),
            )
        )
        await events.append(
            ActionCompleted(
                run_id=run_id,
                actor="gateway",
                action_id=request.action_id,
                evidence_id=result.evidence_id,
                duration_ms=1,
                http_status=200,
                response_sha256=result.response_sha256,
            )
        )
        responses.append((request, result))
    (newer,) = await state.record_response(*responses[1])
    (older,) = await state.record_response(*responses[0])
    assert older.status == "superseded"
    assert older.superseded_by_fact_id == newer.fact_id
    current = await state.query(run_id, predicate="status")
    assert len(current) == 1 and current[0].object_value == "refunded"


@pytest.mark.asyncio
async def test_controller_list_relationships_keep_distinct_objects() -> None:
    events = MemoryEvents()
    state = EventWorldState(events)
    run_id, workspace, first, second = (uuid4() for _ in range(4))
    request = ActionRequest(
        run_id=run_id,
        kind="http_request",
        destination="saas",
        method="GET",
        path=f"/api/workspaces/{workspace}/documents",
    )
    result = _result(
        request,
        {
            "items": [
                {"id": str(first), "workspace_id": str(workspace)},
                {"id": str(second), "workspace_id": str(workspace)},
            ]
        },
    )
    await events.append(
        ActionRequested(
            run_id=run_id,
            actor="gateway",
            action_id=request.action_id,
            action_type="http_request",
            destination="saas",
            method="GET",
            path_sha256=hashlib.sha256(request.path.encode()).hexdigest(),
            range_instance_id=uuid4(),
            range_generation=0,
            request_artifact_id=uuid4(),
        )
    )
    await events.append(
        ActionCompleted(
            run_id=run_id,
            actor="gateway",
            action_id=request.action_id,
            evidence_id=result.evidence_id,
            duration_ms=1,
            http_status=200,
            response_sha256=result.response_sha256,
        )
    )
    await state.record_response(request, result)
    contains = await state.query(run_id, predicate="contains_document")
    assert {fact.object_value.entity_id for fact in contains} == {first, second}


def test_finding_tool_uses_canonical_categories_and_legacy_aliases_are_exact() -> None:
    asset_id, run_id, action_id, evidence_id = (uuid4() for _ in range(4))
    data = {
        "claim": "Cross-tenant document access",
        "asset_id": asset_id,
        "evidence": (
            EvidenceRef(action_id=action_id, evidence_id=evidence_id, description="HTTP 200"),
        ),
        "root_cause_hypothesis": None,
        "subject_role": "member",
        "action": "GET /api/documents/{id}",
        "resource_type": "document",
        "object_relation": "foreign_workspace",
        "expected": "deny",
    }
    assert AuthorizationFindingArgs.model_validate(data).object_relation == "foreign_workspace"
    with pytest.raises(ValidationError):
        AuthorizationFindingArgs.model_validate(
            {**data, "object_relation": "different_workspace (no membership)"}
        )
    with pytest.raises(ValidationError, match="resource_type must match"):
        AuthorizationFindingArgs.model_validate({**data, "resource_type": "ticket"})
    old = CandidateFinding(
        finding_id=uuid4(),
        run_id=run_id,
        range_instance_id=uuid4(),
        range_generation=0,
        family="object_authorization",
        security_property=AuthorizationExpectation(
            subject_role="member",
            action="GET /api/documents/{id}",
            resource_type="document",
            object_relation="different_workspace (no membership)",
            expected="deny",
        ),
        **{key: data[key] for key in ("claim", "asset_id", "evidence", "root_cause_hypothesis")},
    )
    normalized, changes = normalize_legacy_m5v1(old)
    assert normalized.security_property.object_relation == "foreign_workspace"
    assert changes == {
        "object_relation": "different_workspace (no membership) -> foreign_workspace"
    }
    assert normalized.evidence == old.evidence and normalized.finding_id == old.finding_id
    transition = TransitionFindingArgs.model_validate(
        {
            **{
                key: data[key] for key in ("claim", "asset_id", "evidence", "root_cause_hypothesis")
            },
            "subject_role": "member",
            "action": "POST /api/invoices/{id}/refund",
            "resource_type": "invoice",
            "object_relation": "own_workspace",
            "from_state": "paid",
            "to_state": "refunded",
            "expected": "deny",
            "allowed_roles": ["platform_admin", "workspace_admin"],
        }
    )
    assert transition.allowed_roles == ("workspace_admin", "platform_admin")


def test_openrouter_pin_is_frozen_into_request() -> None:
    provider = OpenRouterResponsesProvider("test-key")
    model = ModelSpec(
        provider="openrouter",
        name="moonshotai/kimi-k3",
        reasoning="high",
        upstream_provider="together",
    )
    payload = provider.prepare_request(model, "instructions", [], [], 100)
    assert payload["provider"] == {"order": ["together"], "allow_fallbacks": False}
    with pytest.raises(ValidationError, match="only for OpenRouter"):
        ModelSpec(provider="openai", name="example", upstream_provider="together")


def test_m52_diagnostic_arms_share_model_range_and_budget() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "experiments" / "configs"
    transcript = ExperimentSpec.model_validate(
        yaml.safe_load((root / "kimi-k3-transcript-m52.yaml").read_text())
    )
    structured = ExperimentSpec.model_validate(
        yaml.safe_load((root / "kimi-k3-structured-m52.yaml").read_text())
    )
    assert transcript.model.upstream_provider == structured.model.upstream_provider == "moonshotai"
    left = transcript.model_dump(mode="json", exclude={"name", "memory"})
    right = structured.model_dump(mode="json", exclude={"name", "memory"})
    assert left == right
