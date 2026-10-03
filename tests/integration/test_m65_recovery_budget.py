"""A combined controller allowance cannot enlarge frozen probe admission."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.reporter_recovery import combined_budget
from offsecgym.schemas.events import ModelCallStarted
from offsecgym.schemas.scheduler import AdmissionTask, TaskBudgetRequest
from offsecgym.schemas.specs import Budget
from offsecgym.storage.controller import PostgresControllerState
from offsecgym.storage.event_store import PostgresEventStore


@pytest.mark.postgres
async def test_reporter_reserve_does_not_fund_extra_probe_tasks() -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    try:
        controller = PostgresControllerState(PostgresEventStore(engine))
        run_id = uuid4()
        probe = Budget(
            max_total_tokens=120_000,
            max_model_calls=20,
            max_actions=60,
            max_http_requests=60,
            max_workers=6,
            max_concurrency=1,
            max_wall_seconds=600,
        )
        reporter = Budget(
            max_total_tokens=80_000,
            max_model_calls=4,
            max_output_tokens_per_call=4096,
            max_wall_seconds=300,
        )
        global_budget = combined_budget(probe, reporter)
        capacity = await controller.admission_capacity(run_id, probe, global_budget)
        assert capacity == {"tokens": 120_000, "calls": 20, "actions": 60, "http": 60}
        request = TaskBudgetRequest(
            minimum_viable_tokens=65_000,
            preferred_tokens=65_000,
            max_tokens=65_000,
            minimum_model_calls=2,
            max_model_calls=3,
            expected_actions=2,
            max_actions=3,
            expected_http_requests=2,
            max_http_requests=3,
        )
        tasks = tuple(
            AdmissionTask(
                worker_id=uuid4(),
                task_id=uuid4(),
                kind=kind,
                phase="ready",
                request=request,
            )
            for kind in ("invoice", "document")
        )
        states = {"invoice": "READY", "document": "READY"}
        minimum = {"invoice": 65_000, "document": 65_000}
        utility = {"invoice": 1, "document": 1}
        assert not await controller.reconcile_admission(
            run_id, tasks, states, minimum, utility, probe, global_budget
        )
        assert await controller.reconcile_admission(
            run_id, tasks[:1], states, minimum, utility, probe, global_budget
        )
        reporter_run_id, call_id = uuid4(), uuid4()
        await controller.admission_capacity(reporter_run_id, probe, global_budget)
        started = ModelCallStarted(
            run_id=reporter_run_id,
            actor="reporter",
            call_id=call_id,
            provider="fake",
            model="fake",
            input_sha256="a" * 64,
            request_artifact_id=uuid4(),
            request_sha256="a" * 64,
        )
        assert (
            await controller.reserve_model_call(
                reporter_run_id,
                call_id,
                global_budget,
                reserved_input_tokens=1000,
                reserved_output_tokens=2000,
                request_bytes=2000,
                started_event=started,
            )
            is None
        )
        await controller.settle_model_call(
            reporter_run_id,
            call_id,
            actual_input_tokens=900,
            actual_output_tokens=500,
        )
        assert (await controller.snapshot(reporter_run_id))["reserved_tokens"] == 0
    finally:
        await engine.dispose()
