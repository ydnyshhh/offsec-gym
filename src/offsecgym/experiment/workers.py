"""Run sequential or matched workers through the ordinary experiment lifecycle."""

import asyncio

from offsecgym.experiment.bootstrap import PrerequisiteBootstrap
from offsecgym.experiment.monolithic import MonolithicExperimentRunner
from offsecgym.experiment.scripted import BoundFindingSink, BoundGatewayTools
from offsecgym.schemas.domain import AgentContext
from offsecgym.schemas.specs import Budget, ExperimentSpec
from offsecgym.solver.elastic_workers import ElasticWorkerCoordinator
from offsecgym.solver.matched_workers import MatchedWorkerCoordinator
from offsecgym.solver.scripted import ExperimentInfrastructureError
from offsecgym.solver.workers import SequentialWorkerCoordinator


class WorkerExperimentRunner(MonolithicExperimentRunner):
    def _supported(self, spec: ExperimentSpec) -> bool:
        return (
            spec.range.family == "saas"
            and spec.orchestrator
            in {
                "ephemeral_workers",
                "matched_sequential_workers",
                "matched_parallel_workers",
                "bootstrapped_sequential_workers",
                "bootstrapped_parallel_workers",
                "escrowed_sequential_workers",
                "escrowed_parallel_workers",
                "elastic_sequential_workers",
            }
            and spec.memory == "structured"
            and spec.validation == "deterministic"
            and spec.model is not None
            and spec.surface_visibility == "known_routes"
        )

    def _controller_budget(self, spec: ExperimentSpec) -> Budget:
        if spec.bootstrap_budget is None:
            return spec.budget
        assert spec.budget.max_actions is not None
        assert spec.budget.max_http_requests is not None
        return Budget.model_validate(
            {
                **spec.budget.model_dump(),
                "max_actions": spec.budget.max_actions + spec.bootstrap_budget.max_actions,
                "max_http_requests": (
                    spec.budget.max_http_requests + spec.bootstrap_budget.max_http_requests
                ),
            }
        )

    async def _before_agent(
        self, spec: ExperimentSpec, context: AgentContext, tools: BoundGatewayTools
    ) -> None:
        if spec.bootstrap_budget is None:
            return
        try:
            await asyncio.wait_for(
                PrerequisiteBootstrap(self.events).run(context, tools, spec.bootstrap_budget),
                timeout=spec.bootstrap_budget.max_wall_seconds,
            )
        except TimeoutError as exc:
            raise ExperimentInfrastructureError("bootstrap_wall_time_exhausted") from exc

    def _agent(self, findings_store: BoundFindingSink, spec: ExperimentSpec):
        if spec.model is None:
            raise ValueError("worker coordinator requires a model")
        coordinator_type = (
            SequentialWorkerCoordinator
            if spec.orchestrator == "ephemeral_workers"
            else ElasticWorkerCoordinator
            if spec.orchestrator == "elastic_sequential_workers"
            else MatchedWorkerCoordinator
        )
        return coordinator_type(
            self.provider,
            spec.model,
            findings_store,
            self.events,
            self.runtime.state.root,
            **(
                {
                    "parallel": spec.orchestrator
                    in {
                        "matched_parallel_workers",
                        "bootstrapped_parallel_workers",
                        "escrowed_parallel_workers",
                    },
                    "escrow": spec.orchestrator.startswith("escrowed_"),
                }
                if coordinator_type is MatchedWorkerCoordinator
                else {}
            ),
        )
