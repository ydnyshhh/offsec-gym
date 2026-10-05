"""Versioned monolithic control with the worker study's prerequisite bootstrap."""

from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.experiment.workers import WorkerExperimentRunner
from offsecgym.runtime.enterprise import FAMILY as ENTERPRISE_FAMILY
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import MonolithicSecurityAgent


class BootstrappedMonolithicExperimentRunner(WorkerExperimentRunner):
    """Reuse the frozen worker bootstrap/accounting boundary with one agent."""

    def _supported(self, spec: ExperimentSpec) -> bool:
        return (
            spec.range.family in {"saas", ENTERPRISE_FAMILY}
            and spec.orchestrator == "bootstrapped_monolithic"
            and spec.memory == "structured"
            and spec.validation == "deterministic"
            and spec.model is not None
            and spec.surface_visibility == "known_routes"
            and spec.bootstrap_budget is not None
        )

    def _agent(self, findings_store: BoundFindingSink, spec: ExperimentSpec):
        if spec.model is None:
            raise ValueError("bootstrapped monolithic control requires a model")
        return MonolithicSecurityAgent(
            self.provider,
            spec.model,
            findings_store,
            self.events,
            self.runtime.state.root,
            memory="structured",
            bootstrap_context=True,
        )
