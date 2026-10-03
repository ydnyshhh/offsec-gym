"""Continue the frozen M6.5 control with post-run monolithic coverage closure.

Protocol amendment 1 changes only terminal bookkeeping: a monolithic agent's
active coverage claims are released after RunCompleted and before the pinned
collector checks for outstanding reservations. The probe, provider, range,
validator, score, manifest, and cumulative cost threshold remain unchanged.
"""

from __future__ import annotations

from uuid import UUID

from offsecgym.research import m65_monolithic_execute as collector
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
    world = EventWorldState(events)
    count = 0
    for claim_id, task_id in projection.active_coverage.items():
        await world.update_coverage(run_id, claim_id, task_id, "released")
        count += 1
    return count


class CoverageClosingMonolithicRunner(collector.BootstrappedMonolithicExperimentRunner):
    async def run(self, spec):
        outcome = await super().run(spec)
        await close_completed_monolithic_coverage(self.events, outcome.run_id)
        return outcome


def main() -> None:
    collector.BootstrappedMonolithicExperimentRunner = CoverageClosingMonolithicRunner
    collector.main()


if __name__ == "__main__":
    main()
