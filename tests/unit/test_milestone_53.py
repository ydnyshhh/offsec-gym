"""M5.3 detail retention, active evidence, and exact entity lookup."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from test_milestone_3_runner import MemoryEvents
from test_milestone_52 import _result

from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import EntityRef
from offsecgym.schemas.events import ActionCompleted, ActionRequested
from offsecgym.schemas.specs import ExperimentSpec, ModelSpec
from offsecgym.solver.monolithic import GetEntityArgs, MonolithicSaasAgent, model_tools
from offsecgym.worldview import EventWorldState, WorldContextBuilder
from offsecgym.worldview.extract import MAX_BODY_EXCERPT, ResponseFactExtractor
from offsecgym.worldview.working_set import ActiveWorkingSet, action_target


def test_document_and_ticket_details_keep_bounded_body_and_invoice_mentions() -> None:
    extractor = ResponseFactExtractor()
    run_id, identity, invoice, document, ticket = (uuid4() for _ in range(5))
    for object_type, path in (
        ("document", f"/api/documents/{document}"),
        ("ticket", f"/api/support/tickets/{ticket}"),
    ):
        object_id = document if object_type == "document" else ticket
        request = ActionRequest(
            run_id=run_id,
            kind="http_request",
            destination="saas",
            method="GET",
            path=path,
            identity_id=identity,
        )
        body = f"Please review invoice {invoice}." + ("x" * 700)
        facts = extractor.extract(request, _result(request, {"id": str(object_id), "body": body}))
        by_predicate = {fact.predicate: fact.object_value for fact in facts}
        assert by_predicate["body_excerpt"] == body[:MAX_BODY_EXCERPT]
        assert by_predicate["body_truncated"] is True
        assert by_predicate["mentions_invoice"] == EntityRef(
            entity_id=invoice, entity_type="invoice"
        )
        unrelated = extractor.extract(
            request,
            _result(request, {"id": str(object_id), "body": f"Unrelated {invoice}"}),
        )
        assert "mentions_invoice" not in {fact.predicate for fact in unrelated}


async def _record(state, events, request, result):
    await events.append(
        ActionRequested(
            run_id=request.run_id,
            actor="gateway",
            action_id=request.action_id,
            action_type="http_request",
            destination="saas",
            method=request.method,
            path_sha256=hashlib.sha256(request.path.encode()).hexdigest(),
            identity_id=request.identity_id,
            range_instance_id=uuid4(),
            range_generation=0,
            request_artifact_id=uuid4(),
        )
    )
    await events.append(
        ActionCompleted(
            run_id=request.run_id,
            actor="gateway",
            action_id=request.action_id,
            evidence_id=result.evidence_id,
            duration_ms=1,
            http_status=result.http_status,
            response_sha256=result.response_sha256,
        )
    )
    return await state.record_response(request, result)


@pytest.mark.asyncio
async def test_working_set_keeps_before_after_and_groups_evidence_by_exact_entity(
    tmp_path: Path,
) -> None:
    events = MemoryEvents()
    state = EventWorldState(events)
    run_id, invoice_a, invoice_b, identity = (uuid4() for _ in range(4))
    working = ActiveWorkingSet(max_entities=2, max_actions_per_entity=3)
    recorded = []
    for invoice, status in (
        (invoice_a, "paid"),
        (invoice_b, "paid"),
        (invoice_a, "refunded"),
    ):
        request = ActionRequest(
            run_id=run_id,
            kind="http_request",
            destination="saas",
            method="GET",
            path=f"/api/invoices/{invoice}",
            identity_id=identity,
        )
        result = _result(request, {"id": str(invoice), "status": status})
        facts = await _record(state, events, request, result)
        working.observe(request, result, facts)
        recorded.append((request, result, facts))
    assert action_target(recorded[0][0]) == EntityRef(entity_type="invoice", entity_id=invoice_a)
    assert working.snapshot()[0][0].entity_id == invoice_a
    lines, selected = working.render(max_chars=3500, max_facts=18)
    rendered = "\n".join(lines)
    assert len(rendered) <= 3500
    assert f"- invoice:{invoice_a}" in rendered
    assert f"- invoice:{invoice_b}" in rendered
    assert 'status="paid"' in rendered and 'status="refunded"' in rendered
    for request, result, _ in recorded:
        assert f"action={request.action_id} evidence={result.evidence_id}" in rendered
    assert recorded[0][2][0].fact_id in selected
    context = await WorldContextBuilder(state, events).build(
        run_id, str(invoice_a), working_set=working
    )
    assert 'status="paid"' in context.text and 'status="refunded"' in context.text
    assert recorded[0][2][0].fact_id in context.fact_ids

    agent = MonolithicSaasAgent(
        provider=None,
        model=ModelSpec(provider="openrouter", name="test-model"),
        findings=None,
        events=events,
        state_root=tmp_path,
        memory="structured",
    )
    found = await agent._dispatch_world(
        "get_entity",
        GetEntityArgs(entity_type="invoice", entity_id=invoice_a),
        run_id,
        uuid4(),
        uuid4(),
    )
    assert found["found"] is True
    entity = found["entity"]
    assert entity["attributes"]["status"] == "refunded"
    groups = entity["evidence_groups"]
    assert {group["action_id"] for group in groups} == {
        str(recorded[0][0].action_id),
        str(recorded[2][0].action_id),
    }
    assert str(recorded[1][0].action_id) not in {group["action_id"] for group in groups}
    assert any(
        field["predicate"] == "status" and field["value"] == "paid"
        for group in groups
        for field in group["fields"]
    )
    missing = await agent._dispatch_world(
        "get_entity",
        GetEntityArgs(entity_type="invoice", entity_id=uuid4()),
        run_id,
        uuid4(),
        uuid4(),
    )
    assert missing == {"found": False, "entity": None}


def test_m53_config_is_pinned_and_exact_lookup_tool_is_strict() -> None:
    root = Path(__file__).resolve().parents[2] / "experiments" / "configs"
    m52 = ExperimentSpec.model_validate(
        yaml.safe_load((root / "kimi-k3-structured-m52.yaml").read_text())
    )
    m53 = ExperimentSpec.model_validate(
        yaml.safe_load((root / "kimi-k3-structured-m53.yaml").read_text())
    )
    assert m53.model_dump(mode="json", exclude={"name"}) == m52.model_dump(
        mode="json", exclude={"name"}
    )
    tool = next(item for item in model_tools(structured=True) if item["name"] == "get_entity")
    assert tool["strict"] is True
    assert tool["parameters"]["required"] == ["entity_type", "entity_id"]


def test_working_set_bounds_entities_and_keeps_denied_action_evidence() -> None:
    working = ActiveWorkingSet(max_entities=2, max_actions_per_entity=2)
    run_id, identity = uuid4(), uuid4()
    invoices = [uuid4() for _ in range(3)]
    for invoice in invoices:
        request = ActionRequest(
            run_id=run_id,
            kind="http_request",
            destination="saas",
            method="GET",
            path=f"/api/invoices/{invoice}",
            identity_id=identity,
        )
        result = _result(request, {"error": "forbidden"}, status=403)
        working.observe(request, result, ())
    assert [entity.entity_id for entity, _ in working.snapshot()] == [
        invoices[2],
        invoices[1],
    ]
    rendered, selected = working.render(max_chars=1600, max_facts=5)
    assert "HTTP 403" in "\n".join(rendered)
    assert "evidence=" in "\n".join(rendered)
    assert selected == ()
