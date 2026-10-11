"""Fresh M6.6 monolithic control after a model-free critical-state checkpoint."""

from offsecgym.experiment.bootstrapped_monolithic import BootstrappedMonolithicExperimentRunner
from offsecgym.experiment.scripted import BoundGatewayTools
from offsecgym.research.m661_checkpoints import REGISTRY, prepare_checkpoint
from offsecgym.runtime.enterprise import FAMILY
from offsecgym.schemas.domain import AgentContext
from offsecgym.schemas.specs import ExperimentSpec


class CriticalCheckpointExperimentRunner(BootstrappedMonolithicExperimentRunner):
    """Keep the frozen agent, broad bootstrap, task, and validator unchanged."""

    def __init__(self, runtime, events, provider, checkpoint_id: str) -> None:
        super().__init__(runtime, events, provider)
        self.checkpoint = REGISTRY.get(checkpoint_id)

    def _supported(self, spec: ExperimentSpec) -> bool:
        return super()._supported(spec) and spec.range.family == FAMILY

    async def _before_agent(
        self, spec: ExperimentSpec, context: AgentContext, tools: BoundGatewayTools
    ) -> None:
        await super()._before_agent(spec, context, tools)
        await prepare_checkpoint(self.checkpoint, spec, context, self.runtime, self.events)
