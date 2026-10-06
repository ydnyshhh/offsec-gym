"""M6.6 coverage closure is idempotent and strictly post-terminal."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from test_milestone_3_runner import MemoryEvents

from offsecgym.schemas.domain import CoverageClaim
from offsecgym.schemas.events import RunCompleted, RunStarted
from offsecgym.storage.projection import project_controller_events
from offsecgym.worldview import EventWorldState

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
from m66_pilot_coverage import close_completed_monolithic_coverage  # noqa: E402


@pytest.mark.asyncio
async def test_completed_monolithic_claim_closes_without_new_gateway_work() -> None:
    run_id, task_id = uuid4(), uuid4()
    events = MemoryEvents()
    await events.append(RunStarted(run_id=run_id, actor="controller", experiment_hash="test"))
    world = EventWorldState(events)
    await world.claim_coverage(
        CoverageClaim(
            claim_id=uuid4(),
            run_id=run_id,
            task_id=task_id,
            component="invoices",
            objective="read boundary",
        )
    )
    await events.append(RunCompleted(run_id=run_id, actor="controller", status="budget_exhausted"))
    before = len(events.items)
    assert await close_completed_monolithic_coverage(events, run_id) == 1
    assert await close_completed_monolithic_coverage(events, run_id) == 0
    assert len(events.items) == before + 2
    assert [item.type for item in events.items][-2:] == [
        "coverage_updated",
        "coverage_lease_released",
    ]
    assert not project_controller_events(events.items).active_coverage


@pytest.mark.asyncio
async def test_incomplete_run_cannot_be_closed() -> None:
    run_id = uuid4()
    events = MemoryEvents()
    await events.append(RunStarted(run_id=run_id, actor="controller", experiment_hash="test"))
    with pytest.raises(ValueError, match="one completed run"):
        await close_completed_monolithic_coverage(events, run_id)
