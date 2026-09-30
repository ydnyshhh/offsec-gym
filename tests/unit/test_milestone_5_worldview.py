"""Worldview provenance, adjudication, coverage, and selective retrieval."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from test_milestone_3_runner import MemoryEvents, no_docker_runtime
from test_milestone_4 import QueueProvider, model_spec, turn

from offsecgym.experiment import MonolithicExperimentRunner
from offsecgym.schemas.domain import CoverageClaim, EntityRef, WorldFact
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    ContextRetrieved,
    RunStarted,
    WorldFactAdjudicated,
    WorldFactStateChange,
    WorldFactSubmitted,
    parse_event,
)
from offsecgym.solver.monolithic import model_tools
from offsecgym.worldview import EventWorldState, WorldContextBuilder, WorldStateIntegrityError


async def action_evidence(events: MemoryEvents, run_id: UUID) -> tuple[UUID, UUID]:
    action_id, evidence_id = uuid4(), uuid4()
    await events.append(
        ActionRequested(
            run_id=run_id,
            actor="gateway",
            action_id=action_id,
            action_type="http_request",
            destination="saas",
            method="GET",
            range_instance_id=uuid4(),
            range_generation=1,
            request_artifact_id=uuid4(),
        )
    )
    await events.append(
        ActionCompleted(
            run_id=run_id,
            actor="gateway",
            action_id=action_id,
            evidence_id=evidence_id,
            duration_ms=1,
            http_status=200,
        )
    )
    return action_id, evidence_id


def claim(
    run_id: UUID,
    subject: EntityRef,
    value: object,
    *,
    action_id: UUID | None = None,
    evidence_id: UUID | None = None,
    source_event_id: UUID | None = None,
    kind: str = "observation",
    predicate: str = "requires_authentication",
) -> WorldFact:
    return WorldFact(
        fact_id=uuid4(),
        run_id=run_id,
        kind=kind,
        subject=subject,
        predicate=predicate,
        object_value=value,
        source_event_ids=(source_event_id,) if source_event_id else (),
        source_action_ids=(action_id,) if action_id else (),
        evidence_ids=(evidence_id,) if evidence_id else (),
        confidence=0.8,
    )


@pytest.mark.asyncio
async def test_claims_are_provenance_checked_and_workers_cannot_promote_them() -> None:
    events = MemoryEvents()
    state = EventWorldState(events)
    run_id, other_run = uuid4(), uuid4()
    subject = EntityRef(entity_id=uuid4(), entity_type="endpoint")
    action_id, evidence_id = await action_evidence(events, run_id)
    observed = claim(run_id, subject, True, action_id=action_id, evidence_id=evidence_id)
    with pytest.raises(ValueError, match="enter as"):
        await state.submit_fact(observed.model_copy(update={"status": "validated"}))
    with pytest.raises(ValueError, match="hypothesized claim"):
        WorldFactSubmitted(
            run_id=run_id,
            actor="solver",
            fact=observed.model_copy(update={"status": "validated"}),
        )
    with pytest.raises(ValueError, match="evidence is not bound"):
        await state.submit_fact(observed.model_copy(update={"evidence_ids": (uuid4(),)}))
    with pytest.raises(ValueError, match="source action"):
        await state.submit_fact(observed.model_copy(update={"run_id": other_run}))
    await state.submit_fact(observed)
    assert (await state.adjudicate_fact(run_id, observed.fact_id)).status == "observed"
    assert (await state.query(run_id, "requires_authentication"))[0].fact_id == observed.fact_id
    assert await state.query(other_run) == ()
    assert any(isinstance(item, WorldFactAdjudicated) for item in events.items)
    submitted = next(item for item in events.items if isinstance(item, WorldFactSubmitted))
    assert parse_event(submitted.model_dump(mode="json")) == submitted


@pytest.mark.asyncio
async def test_hypotheses_contradictions_corroboration_and_supersession() -> None:
    events = MemoryEvents()
    state = EventWorldState(events)
    run_id = uuid4()
    source = await events.append(RunStarted(run_id=run_id, actor="controller", experiment_hash="x"))
    subject = EntityRef(entity_id=uuid4(), entity_type="endpoint")
    hypothesis = claim(run_id, subject, False, source_event_id=source.event_id, kind="hypothesis")
    await state.submit_fact(hypothesis)
    assert (await state.adjudicate_fact(run_id, hypothesis.fact_id)).status == "hypothesized"
    first_action, first_evidence = await action_evidence(events, run_id)
    cited_hypothesis = claim(
        run_id,
        subject,
        "This may be vulnerable",
        action_id=first_action,
        evidence_id=first_evidence,
        kind="hypothesis",
        predicate="security_conclusion",
    )
    await state.submit_fact(cited_hypothesis)
    assert (await state.adjudicate_fact(run_id, cited_hypothesis.fact_id)).status == "hypothesized"
    observation = claim(run_id, subject, True, action_id=first_action, evidence_id=first_evidence)
    await state.submit_fact(observation)
    assert (await state.adjudicate_fact(run_id, observation.fact_id)).status == "observed"
    facts = {fact.fact_id: fact for fact in await state.query(run_id)}
    assert facts[hypothesis.fact_id].status == "contradicted"
    assert hypothesis.fact_id in facts[observation.fact_id].contradicts_fact_ids
    second_action, second_evidence = await action_evidence(events, run_id)
    confirmation = claim(
        run_id, subject, True, action_id=second_action, evidence_id=second_evidence
    )
    await state.submit_fact(confirmation)
    assert (await state.adjudicate_fact(run_id, confirmation.fact_id)).status == "corroborated"
    facts = {fact.fact_id: fact for fact in await state.query(run_id)}
    assert facts[observation.fact_id].status == "corroborated"
    assert confirmation.fact_id not in facts[observation.fact_id].contradicts_fact_ids
    third_action, third_evidence = await action_evidence(events, run_id)
    replacement = claim(
        run_id, subject, False, action_id=third_action, evidence_id=third_evidence
    ).model_copy(update={"supersedes_fact_id": observation.fact_id})
    await state.submit_fact(replacement)
    await state.adjudicate_fact(run_id, replacement.fact_id)
    all_facts = {fact.fact_id: fact for fact in await state.query(run_id, include_superseded=True)}
    assert all_facts[observation.fact_id].status == "superseded"
    assert observation.fact_id not in {fact.fact_id for fact in await state.query(run_id)}


def test_structured_tool_schemas_remain_strict() -> None:
    tools = model_tools(structured=True)
    assert len(tools) == 9
    for tool in tools:
        schema = tool["parameters"]
        assert tool["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.asyncio
async def test_conflicting_gateway_observations_are_both_marked_contradicted() -> None:
    events = MemoryEvents()
    state = EventWorldState(events)
    run_id = uuid4()
    subject = EntityRef(entity_id=uuid4(), entity_type="object")
    action_a, evidence_a = await action_evidence(events, run_id)
    first = claim(run_id, subject, "open", action_id=action_a, evidence_id=evidence_a)
    await state.submit_fact(first)
    await state.adjudicate_fact(run_id, first.fact_id)
    action_b, evidence_b = await action_evidence(events, run_id)
    second = claim(run_id, subject, "closed", action_id=action_b, evidence_id=evidence_b)
    await state.submit_fact(second)
    assert (await state.adjudicate_fact(run_id, second.fact_id)).status == "contradicted"
    facts = {fact.fact_id: fact for fact in await state.query(run_id)}
    assert facts[first.fact_id].status == "contradicted"
    assert first.fact_id in facts[second.fact_id].contradicts_fact_ids
    assert second.fact_id in facts[first.fact_id].contradicts_fact_ids


@pytest.mark.asyncio
async def test_coverage_and_context_retrieval_are_bounded_and_rebuildable() -> None:
    events = MemoryEvents()
    state = EventWorldState(events)
    run_id, task_id = uuid4(), uuid4()
    source = await events.append(RunStarted(run_id=run_id, actor="controller", experiment_hash="x"))
    for index in range(20):
        subject = EntityRef(entity_id=uuid4(), entity_type="endpoint")
        fact = claim(
            run_id,
            subject,
            f"GET /api/{'invoices' if index == 17 else 'documents'}/{index}",
            source_event_id=source.event_id,
            kind="hypothesis",
            predicate="route",
        )
        await state.submit_fact(fact)
        await state.adjudicate_fact(run_id, fact.fact_id)
    coverage = CoverageClaim(
        claim_id=uuid4(),
        run_id=run_id,
        task_id=task_id,
        component="billing",
        objective="test invoice access",
    )
    await state.claim_coverage(coverage)
    with pytest.raises(ValueError, match="already actively claimed"):
        await state.claim_coverage(coverage.model_copy(update={"claim_id": uuid4()}))
    builder = WorldContextBuilder(state, events)
    context = await builder.build(run_id, "invoice billing", max_facts=3, max_chars=700)
    assert len(context.fact_ids) <= 3
    assert len(context.text) <= 700
    assert "invoices" in context.text
    assert "active coverage" in context.text
    assert "documents/0" not in context.text
    retrieved = [item for item in events.items if isinstance(item, ContextRetrieved)]
    assert retrieved[-1].selected_fact_ids == context.fact_ids
    with pytest.raises(ValueError, match="owned by another"):
        await state.update_coverage(run_id, coverage.claim_id, uuid4(), "completed")
    assert (
        await state.update_coverage(run_id, coverage.claim_id, task_id, "completed")
    ).status == "completed"
    assert (await EventWorldState(events).coverage(run_id))[0].status == "completed"


@pytest.mark.asyncio
async def test_corrupt_worldview_projection_is_an_unscored_infrastructure_failure(
    tmp_path, monkeypatch
) -> None:
    events = MemoryEvents()
    run_id = uuid4()
    absent_fact_id = uuid4()
    await events.append(
        WorldFactAdjudicated(
            run_id=run_id,
            actor="worldstate",
            fact_id=absent_fact_id,
            changes=(
                WorldFactStateChange(fact_id=absent_fact_id, status="observed", reason_code="test"),
            ),
        )
    )
    with pytest.raises(WorldStateIntegrityError, match="missing fact"):
        await EventWorldState(events).query(run_id)

    async def corrupt_context(self, run_id, query, **kwargs):
        raise WorldStateIntegrityError("corrupt world event stream")

    monkeypatch.setattr(WorldContextBuilder, "build", corrupt_context)
    spec = model_spec(tokens=500, calls=1).model_copy(update={"memory": "structured"})
    outcome = await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), events, QueueProvider(turn())
    ).run(spec)
    assert outcome.evaluation.status == "environment_failed"
    assert outcome.evaluation.score_valid is False
    assert outcome.failure_reason == "WorldStateIntegrityError"


@pytest.mark.asyncio
async def test_structured_agent_retrieves_facts_without_replaying_old_model_turns(
    tmp_path, monkeypatch
) -> None:
    subject_id = uuid4()
    first_call = {
        "type": "function_call",
        "call_id": "hypothesis_1",
        "name": "submit_hypothesis",
        "arguments": json.dumps(
            {
                "subject_type": "object",
                "subject_id": str(subject_id),
                "predicate": "invoice_access",
                "value_text": "May cross a tenant boundary",
                "confidence": 0.4,
                "source_action_id": None,
                "evidence_id": None,
            }
        ),
    }
    second_call = {
        "type": "function_call",
        "call_id": "query_1",
        "name": "query_worldview",
        "arguments": json.dumps({"query": "invoice access", "kind": "hypothesis"}),
    }
    provider = QueueProvider(
        turn({"type": "reasoning", "encrypted_content": "first-turn-only"}, first_call),
        turn(second_call),
        turn(),
    )
    events = MemoryEvents()
    spec = model_spec(tokens=500, calls=4).model_copy(update={"memory": "structured"})
    outcome = await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), events, provider
    ).run(spec)
    assert outcome.evaluation.status == "completed"
    facts = await EventWorldState(events).query(outcome.run_id, kind="hypothesis")
    assert len(facts) == 1 and facts[0].status == "hypothesized"
    assert "first-turn-only" in json.dumps(provider.requests[1]["input"])
    assert "first-turn-only" not in json.dumps(provider.requests[2]["input"])
    assert "invoice_access" in json.dumps(provider.requests[2]["input"])
    assert len(provider.requests[2]["input"]) < len(provider.requests[1]["input"]) + 3
    assert any(isinstance(item, ContextRetrieved) for item in events.items)
