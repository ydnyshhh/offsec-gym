"""Post-terminal monolithic coverage closure for the frozen M6.6 pilot.

This operational amendment appends bookkeeping events only after RunCompleted.
It does not change model requests, gateway actions, range state, validation,
scoring, or the frozen experiment specification.
"""

from __future__ import annotations

from uuid import UUID

from offsecgym.schemas.events import RunCompleted, WorkerSpawned
from offsecgym.storage.projection import project_controller_events
from offsecgym.worldview import EventWorldState


async def close_completed_monolithic_coverage(events, run_id: UUID) -> int:
    trace = await events.read_run(run_id)
    if sum(isinstance(item, RunCompleted) for item in trace) != 1:
        raise ValueError("coverage closure requires one completed run")
    if any(isinstance(item, WorkerSpawned) for item in trace):
        raise ValueError("coverage closure applies only to monolithic runs")
    projection = project_controller_events(trace)
    if (
        projection.active_workers
        or projection.active_actions
        or projection.model_reservations
        or any(hold.active for hold in projection.admission_holds.values())
    ):
        raise ValueError("coverage closure cannot mask another live reservation")
    world = EventWorldState(events)
    count = 0
    for claim_id, task_id in projection.active_coverage.items():
        await world.update_coverage(run_id, claim_id, task_id, "released")
        count += 1
    return count
