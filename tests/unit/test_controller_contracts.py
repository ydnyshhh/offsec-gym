"""Small M6.0 controller contracts that do not need PostgreSQL."""

from uuid import uuid4

import pytest

from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.events import (
    ActionAttemptReserved,
    ActionReservationAcquired,
    ActionReservationReleased,
    ModelBudgetReserved,
    ModelBudgetSettled,
    WorkerFinished,
    WorkerSpawned,
    WorkerStarted,
    parse_event,
)
from offsecgym.storage.controller import request_fingerprint
from offsecgym.storage.projection import project_controller_events


def test_fingerprint_canonicalizes_body_and_binds_identity() -> None:
    run_id, identity = uuid4(), uuid4()
    action = ActionRequest(
        run_id=run_id,
        kind="http_request",
        destination="saas",
        method="POST",
        path="/api/invoices/1",
        identity_id=identity,
        json_body={"one": 1, "two": 2},
    )
    reordered = action.model_copy(update={"json_body": {"two": 2, "one": 1}})
    assert request_fingerprint(action) == request_fingerprint(reordered)
    assert request_fingerprint(action) != request_fingerprint(
        action.model_copy(update={"identity_id": uuid4()})
    )
    assert request_fingerprint(action) != request_fingerprint(
        action.model_copy(update={"path": "/api/invoices/2"})
    )


def test_worker_actions_require_task_attribution() -> None:
    with pytest.raises(ValueError, match="task_id"):
        ActionRequest(
            run_id=uuid4(),
            worker_id=uuid4(),
            kind="http_request",
            destination="saas",
            method="GET",
            path="/api/me",
        )


def test_controller_events_round_trip_and_project_state() -> None:
    run_id, worker_id, task_id, action_id, call_id = (uuid4() for _ in range(5))
    fingerprint = "a" * 64
    trace = [
        WorkerSpawned(
            run_id=run_id,
            actor="coordinator",
            worker_id=worker_id,
            task_id=task_id,
            objective="billing",
        ),
        WorkerStarted(run_id=run_id, actor="coordinator", worker_id=worker_id, task_id=task_id),
        ActionAttemptReserved(
            run_id=run_id,
            actor="controller",
            action_id=action_id,
            worker_id=worker_id,
            task_id=task_id,
        ),
        ActionReservationAcquired(
            run_id=run_id,
            actor="controller",
            action_id=action_id,
            fingerprint=fingerprint,
            worker_id=worker_id,
            task_id=task_id,
        ),
        ActionReservationReleased(
            run_id=run_id,
            actor="controller",
            action_id=action_id,
            fingerprint=fingerprint,
            worker_id=worker_id,
            task_id=task_id,
        ),
        ModelBudgetReserved(
            run_id=run_id,
            actor="controller",
            call_id=call_id,
            reserved_tokens=50,
            reserved_cost_microusd=60,
            worker_id=worker_id,
            task_id=task_id,
        ),
        ModelBudgetSettled(
            run_id=run_id,
            actor="controller",
            call_id=call_id,
            actual_tokens=30,
            actual_cost_microusd=40,
            worker_id=worker_id,
            task_id=task_id,
        ),
        WorkerFinished(
            run_id=run_id,
            actor="coordinator",
            worker_id=worker_id,
            task_id=task_id,
            status="completed",
        ),
    ]
    assert [parse_event(item.model_dump(mode="json")) for item in trace] == trace
    projected = project_controller_events(trace)
    assert projected.used_actions == projected.used_http_requests == 1
    assert projected.used_model_calls == 1
    assert projected.used_tokens == 30
    assert projected.reserved_tokens == 0
    assert projected.used_cost_microusd == 40
    assert projected.spawned_workers == 1 and projected.active_workers == 0
    assert projected.worker_status[worker_id] == "finished"
    assert projected.active_actions == {}
