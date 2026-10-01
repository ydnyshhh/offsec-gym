"""Run sequential or matched workers through the ordinary experiment lifecycle."""

from offsecgym.experiment.monolithic import MonolithicExperimentRunner
from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.matched_workers import MatchedWorkerCoordinator
from offsecgym.solver.workers import SequentialWorkerCoordinator


class WorkerExperimentRunner(MonolithicExperimentRunner):
    def _supported(self, spec: ExperimentSpec) -> bool:
        return (
            spec.range.family == "saas"
            and spec.orchestrator
            in {"ephemeral_workers", "matched_sequential_workers", "matched_parallel_workers"}
            and spec.memory == "structured"
            and spec.validation == "deterministic"
            and spec.model is not None
            and spec.surface_visibility == "known_routes"
        )

    def _agent(self, findings_store: BoundFindingSink, spec: ExperimentSpec):
        if spec.model is None:
            raise ValueError("worker coordinator requires a model")
        coordinator_type = (
            SequentialWorkerCoordinator
            if spec.orchestrator == "ephemeral_workers"
            else MatchedWorkerCoordinator
        )
        return coordinator_type(
            self.provider,
            spec.model,
            findings_store,
            self.events,
            self.runtime.state.root,
            **(
                {"parallel": spec.orchestrator == "matched_parallel_workers"}
                if coordinator_type is MatchedWorkerCoordinator
                else {}
            ),
        )
