import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.schemas.domain import (
    AuthorizationExpectation,
    CandidateFinding,
    CoverageClaim,
    EntityRef,
    EvidenceRef,
    ValidationResult,
    WorldFact,
)
from offsecgym.schemas.events import (
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    RunStarted,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.worldview import EventWorldState, WorldContextBuilder


@pytest.mark.postgres
async def test_postgres_event_store_orders_and_deduplicates() -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    try:
        store = PostgresEventStore(engine)
        run_id = uuid4()
        first = RunStarted(run_id=run_id, actor="test", experiment_hash="sha256:one")
        second = RunStarted(run_id=run_id, actor="test", experiment_hash="sha256:two")
        written_first = await store.append(first)
        assert written_first.sequence_number == 1
        assert (await store.append(first)) == written_first
        with pytest.raises(ValueError, match="different content"):
            await store.append(first.model_copy(update={"experiment_hash": "sha256:changed"}))
        assert (await store.append(second)).sequence_number == 2
        assert await store.read_run(run_id) == [
            written_first,
            second.model_copy(update={"sequence_number": 2}),
        ]
    finally:
        await engine.dispose()


@pytest.mark.postgres
async def test_postgres_persists_candidate_and_validation_events() -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    try:
        store = PostgresEventStore(engine)
        run_id = uuid4()
        finding = CandidateFinding(
            finding_id=uuid4(),
            run_id=run_id,
            range_instance_id=uuid4(),
            range_generation=0,
            claim="Synthetic authorization boundary crossed",
            family="object_authorization",
            asset_id=uuid4(),
            security_property=AuthorizationExpectation(
                subject_role="member",
                action="GET /api/documents/{id}",
                resource_type="document",
                object_relation="foreign_workspace",
                expected="deny",
            ),
            evidence=(EvidenceRef(evidence_id=uuid4(), action_id=uuid4(), description="read"),),
        )
        submitted = await store.append(
            FindingSubmitted(run_id=run_id, actor="solver", finding=finding)
        )
        result = ValidationResult(run_id=run_id, finding_id=finding.finding_id, status="rejected")
        checked = await store.append(
            FindingValidated(run_id=run_id, actor="validator", result=result)
        )
        assert await store.read_run(run_id) == [submitted, checked]
    finally:
        await engine.dispose()


@pytest.mark.postgres
async def test_postgres_persists_model_usage_and_provider_failure() -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    try:
        store = PostgresEventStore(engine)
        run_id, call_id = uuid4(), uuid4()
        started = await store.append(
            ModelCallStarted(
                run_id=run_id,
                actor="controller",
                call_id=call_id,
                provider="openai",
                model="mock-model",
                input_sha256="a" * 64,
                request_artifact_id=uuid4(),
                request_sha256="a" * 64,
            )
        )
        completed = await store.append(
            ModelCallCompleted(
                run_id=run_id,
                actor="controller",
                call_id=call_id,
                provider_status="completed",
                tool_call_count=1,
                input_tokens=12,
                output_tokens=4,
                estimated_cost_usd=0.00002,
                response_artifact_id=uuid4(),
                response_sha256="b" * 64,
                causation_id=started.event_id,
            )
        )
        failed = await store.append(
            ModelCallFailed(
                run_id=run_id,
                actor="controller",
                call_id=uuid4(),
                reason_code="provider_server_error",
                http_status=503,
            )
        )
        assert await store.read_run(run_id) == [started, completed, failed]
    finally:
        await engine.dispose()


@pytest.mark.postgres
async def test_postgres_rebuilds_worldview_and_context_from_events() -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    try:
        events = PostgresEventStore(engine)
        state = EventWorldState(events)
        run_id = uuid4()
        source = await events.append(
            RunStarted(run_id=run_id, actor="controller", experiment_hash="worldview-test")
        )
        fact = WorldFact(
            fact_id=uuid4(),
            run_id=run_id,
            kind="hypothesis",
            subject=EntityRef(entity_id=uuid4(), entity_type="endpoint"),
            predicate="route",
            object_value="GET /api/invoices/{id}",
            source_event_ids=(source.event_id,),
            confidence=0.3,
        )
        await state.submit_fact(fact)
        await state.adjudicate_fact(run_id, fact.fact_id)
        coverage = CoverageClaim(
            claim_id=uuid4(),
            run_id=run_id,
            task_id=uuid4(),
            component="billing",
            objective="test invoice access",
        )
        await state.claim_coverage(coverage)
        context = await WorldContextBuilder(state, events).build(run_id, "invoice")
        assert fact.fact_id in context.fact_ids
        rebuilt = EventWorldState(events)
        assert (await rebuilt.query(run_id))[0].fact_id == fact.fact_id
        assert (await rebuilt.coverage(run_id))[0] == coverage
        assert len(await events.read_run(run_id)) == 5
    finally:
        await engine.dispose()
