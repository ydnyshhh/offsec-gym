"""Matched M6.6 control/witness execution over one frozen experiment spec."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from offsecgym.experiment.bootstrapped_monolithic import BootstrappedMonolithicExperimentRunner
from offsecgym.experiment.scripted import ScriptedExperimentOutcome
from offsecgym.experiment.witness_planning import WitnessPlanningExperimentRunner
from offsecgym.interfaces import EventStore
from offsecgym.providers.base import ModelProvider
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.range_surface import range_surface

Arm = Literal["control", "witness"]


@dataclass(frozen=True)
class WitnessPlanningPair:
    build_id: UUID
    arm_order: tuple[Arm, Arm]
    control: ScriptedExperimentOutcome
    witness: ScriptedExperimentOutcome


class WitnessPlanningPairRunner:
    """The two arms differ only by the opt-in witness tool/prompt policy."""

    def __init__(
        self,
        runtime: ComposeRangeRuntime,
        events: EventStore,
        provider_factory: Callable[[], ModelProvider],
    ) -> None:
        self.runtime = runtime
        self.events = events
        self.provider_factory = provider_factory

    async def run_pair(
        self,
        spec: ExperimentSpec,
        *,
        arm_order: tuple[Arm, Arm] = ("control", "witness"),
    ) -> WitnessPlanningPair:
        range_surface(spec.range.family)
        if spec.orchestrator != "bootstrapped_monolithic" or spec.memory != "structured":
            raise ValueError("M6.6 pair requires a bootstrapped structured monolithic spec")
        if set(arm_order) != {"control", "witness"}:
            raise ValueError("M6.6 pair must contain each arm exactly once")
        outcomes = {}
        for arm in arm_order:
            runner_type = (
                BootstrappedMonolithicExperimentRunner
                if arm == "control"
                else WitnessPlanningExperimentRunner
            )
            outcomes[arm] = await runner_type(
                self.runtime, self.events, self.provider_factory()
            ).run(spec)
        control = outcomes["control"]
        witness = outcomes["witness"]
        if control.build_id is None or control.build_id != witness.build_id:
            raise ValueError("M6.6 arms did not run the same range build")
        if control.run_id == witness.run_id:
            raise ValueError("M6.6 arms reused one run identity")
        return WitnessPlanningPair(control.build_id, arm_order, control, witness)
