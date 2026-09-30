"""Stable boundaries; implementations may be swapped in experiments."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import (
    AgentContext,
    AgentResult,
    AgentTask,
    CandidateFinding,
    CoverageClaim,
    ExperimentContext,
    OrchestrationResult,
    RangeControllerMetadata,
    RangeInstanceStatus,
    ValidationContext,
    ValidationResult,
    WorldFact,
)
from offsecgym.schemas.events import AnyTraceEvent
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.schemas.specs import RangeSpec


class ToolRegistry(Protocol):
    async def execute(self, action: ActionRequest) -> ActionResult: ...


class Agent(Protocol):
    async def run(
        self, task: AgentTask, context: AgentContext, tools: ToolRegistry
    ) -> AgentResult: ...


class Orchestrator(Protocol):
    async def run(self, context: ExperimentContext) -> OrchestrationResult: ...


class RangeRuntime(Protocol):
    async def build(self, spec: RangeSpec) -> UUID: ...
    async def create_instance(self, build_id: UUID) -> UUID: ...
    async def start_instance(self, instance_id: UUID) -> RangeInstanceStatus: ...
    async def instance_status(self, instance_id: UUID) -> RangeInstanceStatus: ...
    async def wait_until_healthy(self, instance_id: UUID) -> RangeInstanceStatus: ...
    async def snapshot_metadata(self, instance_id: UUID) -> RangeControllerMetadata: ...
    async def reset_instance(self, instance_id: UUID) -> RangeInstanceStatus: ...
    async def stop_instance(self, instance_id: UUID) -> RangeInstanceStatus: ...
    async def destroy_instance(self, instance_id: UUID) -> RangeInstanceStatus: ...


class ActionGateway(Protocol):
    async def execute(self, action: ActionRequest, context: ExperimentContext) -> ActionResult: ...


class Validator(Protocol):
    async def validate(
        self, finding: CandidateFinding, context: ValidationContext
    ) -> ValidationResult: ...


class OracleStore(Protocol):
    def load_for_context(self, context: ValidationContext) -> GroundTruthManifest: ...


class WorldState(Protocol):
    async def submit_fact(self, fact: WorldFact) -> WorldFact: ...
    async def adjudicate_fact(self, run_id: UUID, fact_id: UUID) -> WorldFact: ...
    async def supersede_fact(
        self, run_id: UUID, old_fact_id: UUID, new_fact_id: UUID, reason_code: str
    ) -> WorldFact: ...
    async def query(self, run_id: UUID, predicate: str | None = None) -> Sequence[WorldFact]: ...
    async def claim_coverage(self, claim: CoverageClaim) -> CoverageClaim: ...
    async def update_coverage(
        self, run_id: UUID, claim_id: UUID, task_id: UUID, status: str
    ) -> CoverageClaim: ...
    async def coverage(self, run_id: UUID) -> Sequence[CoverageClaim]: ...


class EventStore(Protocol):
    async def append(self, event: AnyTraceEvent) -> AnyTraceEvent: ...
    async def read_run(self, run_id: UUID) -> Sequence[AnyTraceEvent]: ...
