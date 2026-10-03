"""Post-run coverage closure leaves completed monolithic evidence intact."""

from __future__ import annotations

from uuid import uuid4

import pytest
from test_milestone_3_runner import MemoryEvents

from offsecgym.schemas.domain import CoverageClaim
from offsecgym.schemas.events import RunCompleted, RunStarted
from offsecgym.storage.projection import project_controller_events
from offsecgym.worldview import EventWorldState
from research_ops.m65_control_continue import close_completed_monolithic_coverage


@pytest.mark.asyncio
async def test_completed_monolithic_claims_close_without_new_actions() -> None:
    run_id, task_id = uuid4(), uuid4()
    events = MemoryEvents()
    await events.append(RunStarted(run_id=run_id, actor="controller", experiment_hash="test"))
    world = EventWorldState(events)
    await world.claim_coverage(
        CoverageClaim(
            claim_id=uuid4(),
            run_id=run_id,
            task_id=task_id,
            component="documents",
            objective="read boundary",
        )
    )
    await events.append(RunCompleted(run_id=run_id, actor="controller", status="budget_exhausted"))
    assert len(project_controller_events(events.items).active_coverage) == 1
    assert await close_completed_monolithic_coverage(events, run_id) == 1
    assert await close_completed_monolithic_coverage(events, run_id) == 0
    projection = project_controller_events(events.items)
    assert not projection.active_coverage
    assert [item.type for item in events.items][-2:] == [
        "coverage_updated",
        "coverage_lease_released",
    ]


@pytest.mark.asyncio
async def test_incomplete_run_cannot_be_closed() -> None:
    run_id = uuid4()
    events = MemoryEvents()
    await events.append(RunStarted(run_id=run_id, actor="controller", experiment_hash="test"))
    with pytest.raises(ValueError, match="completed run"):
        await close_completed_monolithic_coverage(events, run_id)
