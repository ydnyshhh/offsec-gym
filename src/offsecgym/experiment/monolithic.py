"""Run a single model agent through the established SaaS experiment lifecycle."""

from __future__ import annotations

from offsecgym.experiment.scripted import BoundFindingSink, ScriptedExperimentRunner
from offsecgym.interfaces import EventStore
from offsecgym.providers.base import ModelProvider
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import MonolithicSaasAgent


class MonolithicExperimentRunner(ScriptedExperimentRunner):
    def __init__(
        self, runtime: ComposeRangeRuntime, events: EventStore, provider: ModelProvider
    ) -> None:
        super().__init__(runtime, events)
        self.provider = provider

    def _supported(self, spec: ExperimentSpec) -> bool:
        return (
            spec.range.family == "saas"
            and spec.orchestrator == "monolithic"
            and spec.memory == "transcript"
            and spec.validation == "deterministic"
            and spec.model is not None
        )

    def _agent(self, findings_store: BoundFindingSink, spec: ExperimentSpec):
        if spec.model is None:
            raise ValueError("monolithic agent requires a model")
        return MonolithicSaasAgent(self.provider, spec.model, findings_store, self.events)
