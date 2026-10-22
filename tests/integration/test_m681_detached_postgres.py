"""Detached qualification against a disposable, migrated PostgreSQL test DB."""

from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.research.m681_detached_persistence import (
    SyntheticRunContext,
    run_detached_script,
)
from offsecgym.schemas.events import ExperimentStateChanged, RunCompleted
from offsecgym.solver.experiment_state import (
    CompleteExperimentArgs,
    ExperimentStateStore,
    GetActiveExperimentArgs,
    StartExperimentArgs,
    UpdateExperimentArgs,
)
from offsecgym.storage.event_store import PostgresEventStore

OBJECT = UUID("11111111-1111-4111-8111-111111111111")


def arguments(*, targets: list[str] | None = None) -> dict[str, object]:
    return {
        "hypothesis": "Synthetic state remains observable",
        "subject_identity": None,
        "target_objects": targets if targets is not None else [f"project:{OBJECT}"],
        "preconditions": ["Read synthetic baseline"],
        "planned_steps": ["Observe", "Record", "Compare"],
        "expected_positive_observation": "State changed",
        "expected_negative_observation": "State unchanged",
        "required_evidence": ["Synthetic note"],
    }


def _isolated_url() -> str:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    parsed = make_url(url)
    if parsed.database != "offsecgym_test" or parsed.host not in {"localhost", "127.0.0.1"}:
        pytest.fail("detached qualification requires local offsecgym_test database")
    return url


@pytest.mark.postgres
async def test_postgres_detached_lifecycle_durability_isolation_and_terminal():
    url = _isolated_url()
    first_engine = create_async_engine(url)
    second_engine = create_async_engine(url)
    run_a, run_b = uuid4(), uuid4()
    try:
        first_store = PostgresEventStore(first_engine)
        started = await run_detached_script(
            first_store,
            run_id=run_a,
            synthetic_run_context=SyntheticRunContext(),
            scripted_argument_dict=arguments(),
            normalization_enabled=True,
            scripted_operations=(),
        )
        assert len(started.state_events) == 1
        assert started.state_events[0].sequence_number == 2
        assert started.state_events[0].target_objects == (OBJECT,)
        assert started.views[0]["experiment_id"] == str(started.state_events[0].experiment_id)
        await first_engine.dispose()

        second_store = PostgresEventStore(second_engine)
        second_state = ExperimentStateStore(second_store)
        retrieved = await second_state.change(
            run_a, "get_active_experiment", GetActiveExperimentArgs(), causation_id=uuid4()
        )
        assert retrieved["experiment_id"] == started.views[0]["experiment_id"]
        assert retrieved["target_objects"] == [str(OBJECT)]
        assert retrieved["planned_steps"] == ["Observe", "Record", "Compare"]
        assert retrieved["hypothesis"] == arguments()["hypothesis"]
        assert await second_state.active(run_b) is None
        experiment_id = UUID(str(retrieved["experiment_id"]))
        with pytest.raises(ValueError, match="active_experiment_not_found"):
            await second_state.change(
                run_b,
                "update_experiment",
                UpdateExperimentArgs(
                    experiment_id=experiment_id, current_step=1, status="active", blockers=()
                ),
                causation_id=uuid4(),
            )
        assert await second_store.read_run(run_b) == []
        changed = await second_state.change(
            run_a,
            "update_experiment",
            UpdateExperimentArgs(
                experiment_id=experiment_id, current_step=3, status="active", blockers=()
            ),
            causation_id=uuid4(),
        )
        assert changed["current_step"] == 3
        with pytest.raises(ValueError, match="experiment_step_out_of_range"):
            await second_state.change(
                run_a,
                "update_experiment",
                UpdateExperimentArgs(
                    experiment_id=experiment_id, current_step=999, status="active", blockers=()
                ),
                causation_id=uuid4(),
            )
        assert len(await second_store.read_run(run_a)) == 3
        completed = await second_state.change(
            run_a,
            "complete_experiment",
            CompleteExperimentArgs(experiment_id=experiment_id),
            causation_id=uuid4(),
        )
        assert completed["status"] == "completed"
        assert (await second_store.read_run(run_a))[-1].status == "completed"
        assert await second_state.active(run_a) is None
        await second_store.append(
            RunCompleted(run_id=run_a, actor="qualification", status="completed")
        )
        before = await second_store.read_run(run_a)
        with pytest.raises(ValueError, match="experiment cannot change after terminal"):
            await second_state.change(
                run_a,
                "start_experiment",
                StartExperimentArgs.model_validate(arguments(targets=[str(OBJECT)])),
                causation_id=uuid4(),
            )
        assert await second_store.read_run(run_a) == before
        assert [event.sequence_number for event in before] == list(range(1, len(before) + 1))
        assert sum(isinstance(event, ExperimentStateChanged) for event in before) == 3
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


@pytest.mark.postgres
async def test_postgres_serializes_concurrent_duplicate_start():
    url = _isolated_url()
    first_engine = create_async_engine(url)
    second_engine = create_async_engine(url)
    run_id = uuid4()
    try:
        first = ExperimentStateStore(PostgresEventStore(first_engine))
        second = ExperimentStateStore(PostgresEventStore(second_engine))
        args = StartExperimentArgs.model_validate(arguments(targets=[str(OBJECT)]))
        outcomes = await asyncio.gather(
            first.change(run_id, "start_experiment", args, causation_id=uuid4()),
            second.change(run_id, "start_experiment", args, causation_id=uuid4()),
            return_exceptions=True,
        )
        assert sum(isinstance(item, dict) for item in outcomes) == 1
        assert (
            sum(
                isinstance(item, ValueError) and "active_experiment_exists" in str(item)
                for item in outcomes
            )
            == 1
        )
        trace = await first.events.read_run(run_id)
        assert len(trace) == 1
        assert isinstance(trace[0], ExperimentStateChanged)
        assert trace[0].sequence_number == 1
    finally:
        await first_engine.dispose()
        await second_engine.dispose()
