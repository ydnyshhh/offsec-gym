"""Admission holds survive competing controller instances and protect minima."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.schemas.scheduler import AdmissionTask, TaskBudgetRequest
from offsecgym.schemas.specs import Budget
from offsecgym.storage.controller import PostgresControllerState
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events


def _task(kind: str, minimum: int, phase: str) -> AdmissionTask:
    return AdmissionTask(
        worker_id=uuid4(),
        task_id=uuid4(),
        kind=kind,
        phase=phase,
        request=TaskBudgetRequest(
            minimum_viable_tokens=minimum,
            preferred_tokens=minimum,
            max_tokens=80000,
            minimum_model_calls=1,
            max_model_calls=3,
            expected_actions=2,
            max_actions=3,
            expected_http_requests=2,
            max_http_requests=3,
        ),
    )


@pytest.mark.postgres
async def test_competing_controllers_cannot_spend_another_tasks_minimum() -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    first_engine = create_async_engine(url)
    second_engine = create_async_engine(url)
    first_events = PostgresEventStore(first_engine)
    second_events = PostgresEventStore(second_engine)
    first = PostgresControllerState(first_events)
    second = PostgresControllerState(second_events)
    run_id = uuid4()
    budget = Budget(
        max_total_tokens=100000,
        max_model_calls=10,
        max_actions=10,
        max_http_requests=10,
        max_workers=3,
        max_concurrency=1,
    )
    invoice = _task("invoice", 30000, "ready")
    public = _task("public", 30000, "ready")
    refund = _task("refund", 40000, "forecast")
    states = {"invoice": "READY", "public": "READY", "refund": "PENDING"}
    try:
        assert await first.reconcile_admission(
            run_id,
            (invoice, public, refund),
            states,
            {"invoice": 30000, "public": 30000},
            {"invoice": 220, "public": 140},
            budget,
            budget,
        )
        assert await first.admission_capacity(run_id, budget, budget) == {
            "tokens": 100000,
            "calls": 10,
            "actions": 10,
            "http": 10,
        }
        activation = await asyncio.gather(
            first.activate_admitted_task(run_id, invoice, "invoice test"),
            second.activate_admitted_task(run_id, invoice, "invoice test"),
            return_exceptions=True,
        )
        assert sum(item is None for item in activation) == 1
        assert sum(isinstance(item, ValueError) for item in activation) == 1
        assert (
            await first.spawn_worker(
                run_id, invoice.worker_id, invoice.task_id, "invoice test", budget
            )
            is None
        )
        assert await first.start_worker(run_id, invoice.worker_id, invoice.task_id, budget) is None
        rejection = await second.reserve_model_call(
            run_id,
            uuid4(),
            budget,
            reserved_input_tokens=31000,
            reserved_output_tokens=0,
            request_bytes=100,
            worker_id=invoice.worker_id,
            task_id=invoice.task_id,
        )
        assert rejection == "worker_token_escrow_exhausted"
        assert await first.admission_hold_snapshot(run_id) == await second.admission_hold_snapshot(
            run_id
        )
        await first.finish_worker(run_id, invoice.worker_id, invoice.task_id, "budget_exhausted")
        assert await second.reconcile_admission(
            run_id,
            (public, refund.model_copy(update={"phase": "ready"})),
            {"invoice": "COMPLETED", "public": "READY", "refund": "READY"},
            {"public": 30000, "refund": 40000},
            {"public": 140, "refund": 240},
            budget,
            budget,
        )
        assert await first.reconcile_admission(
            run_id,
            (),
            {"invoice": "COMPLETED", "public": "READY", "refund": "READY"},
            {"public": 30000, "refund": 40000},
            {"public": 140, "refund": 240},
            budget,
            budget,
        )
        trace = await first_events.read_run(run_id)
        projection = project_controller_events(trace)
        holds = await first.admission_hold_snapshot(run_id)
        assert all(not row["active"] for row in holds)
        assert all(not item.active for item in projection.admission_holds.values())
        account = (await first.worker_escrow_snapshot(run_id))[0]
        replay = projection.worker_escrows[invoice.worker_id]
        assert account["token_limit"] == replay.token_limit == 0
        assert len(projection.admission_decisions) == 3
    finally:
        await first_engine.dispose()
        await second_engine.dispose()
