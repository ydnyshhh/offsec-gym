import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.schemas.domain import (
    AuthorizationExpectation,
    CandidateFinding,
    EvidenceRef,
    ValidationResult,
)
from offsecgym.schemas.events import FindingSubmitted, FindingValidated, RunStarted
from offsecgym.storage.event_store import PostgresEventStore


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
        result = ValidationResult(finding_id=finding.finding_id, status="rejected")
        checked = await store.append(
            FindingValidated(run_id=run_id, actor="validator", result=result)
        )
        assert await store.read_run(run_id) == [submitted, checked]
    finally:
        await engine.dispose()
