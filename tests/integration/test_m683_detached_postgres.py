"""Disposable PostgreSQL qualification of detached, fabricated contract inputs."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.research.m681_detached_persistence import (
    ScriptedOperation,
    SyntheticRunContext,
    run_detached_script,
)
from offsecgym.research.m683_reference_contracts import compare_contracts
from offsecgym.schemas.events import ExperimentStateChanged, FindingValidated, RunCompleted
from offsecgym.storage.event_store import PostgresEventStore

OBJECT = "11111111-1111-4111-8111-111111111111"


def _arguments(target: str) -> dict[str, object]:
    return {
        "hypothesis": "Fabricated state is observable",
        "subject_identity": None,
        "target_objects": [target],
        "preconditions": [],
        "planned_steps": ["Observe fabricated state", "Record fabricated state"],
        "expected_positive_observation": "Recorded",
        "expected_negative_observation": "Absent",
        "required_evidence": [],
    }


def _isolated_url() -> str:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    parsed = make_url(url)
    if parsed.database != "offsecgym_test" or parsed.host not in {"localhost", "127.0.0.1"}:
        pytest.fail("M6.8.3 requires a disposable local offsecgym_test database")
    return url


@pytest.mark.postgres
async def test_all_accepted_contracts_persist_without_verdict_or_cross_run_leakage() -> None:
    url = _isolated_url()
    first_engine = create_async_engine(url)
    second_engine = create_async_engine(url)
    cases = (("C0", OBJECT), ("C1", f"change_request:{OBJECT}"), ("C2", f"change:{OBJECT}"))
    run_ids = [uuid4() for _ in cases]
    invalid_run = uuid4()
    try:
        first = PostgresEventStore(first_engine)
        for run_id, (contract, target) in zip(run_ids, cases, strict=True):
            compared = compare_contracts(_arguments(target), contracts=(contract,))[contract]
            assert compared.post_normalization_schema_accepted
            assert compared.validated_arguments is not None
            normalized = compared.validated_arguments.model_dump(mode="json")
            recorded = await run_detached_script(
                first,
                run_id=run_id,
                synthetic_run_context=SyntheticRunContext("m683-fabricated"),
                scripted_argument_dict=normalized,
                normalization_enabled=False,
                scripted_operations=(ScriptedOperation("complete"),),
            )
            assert recorded.validated_arguments == compared.validated_arguments
            assert len(recorded.state_events) == 2
        rejected = compare_contracts(_arguments(f"unknown:{OBJECT}"))["C2"]
        assert rejected.validated_arguments is None
        assert await first.read_run(invalid_run) == []
        await first_engine.dispose()
        fresh = PostgresEventStore(second_engine)
        for run_id in run_ids:
            trace = await fresh.read_run(run_id)
            states = [event for event in trace if isinstance(event, ExperimentStateChanged)]
            assert len(states) == 2
            assert [event.sequence_number for event in states] == [2, 3]
            assert all(event.run_id == run_id for event in states)
            assert all(event.target_objects == (UUID(OBJECT),) for event in states)
            assert all(
                event.planned_steps == ("Observe fabricated state", "Record fabricated state")
                for event in states
            )
            assert all(event.hypothesis == "Fabricated state is observable" for event in states)
            assert states[-1].status == "completed"
            assert not any(isinstance(event, (FindingValidated, RunCompleted)) for event in trace)
        assert await fresh.read_run(invalid_run) == []
        assert await fresh.read_run(uuid4()) == []
    finally:
        await first_engine.dispose()
        await second_engine.dispose()
