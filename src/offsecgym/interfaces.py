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
    ExperimentContext,
    OrchestrationResult,
    RangeStatus,
    ValidationContext,
    ValidationResult,
    WorldFact,
)
from offsecgym.schemas.events import AnyTraceEvent
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
    async def start(self, range_id: UUID) -> RangeStatus: ...
    async def status(self, range_id: UUID) -> RangeStatus: ...
    async def wait_until_healthy(self, range_id: UUID) -> RangeStatus: ...
    async def reset(self, range_id: UUID) -> RangeStatus: ...
    async def stop(self, range_id: UUID) -> RangeStatus: ...
    async def destroy(self, range_id: UUID) -> RangeStatus: ...


class ActionGateway(Protocol):
    async def execute(self, action: ActionRequest, context: ExperimentContext) -> ActionResult: ...


class Validator(Protocol):
    async def validate(
        self, finding: CandidateFinding, context: ValidationContext
    ) -> ValidationResult: ...


class WorldState(Protocol):
    async def submit_fact(self, fact: WorldFact) -> WorldFact: ...
    async def adjudicate_fact(self, fact_id: UUID) -> WorldFact: ...
    async def query(self, run_id: UUID, predicate: str) -> Sequence[WorldFact]: ...


class EventStore(Protocol):
    async def append(self, event: AnyTraceEvent) -> AnyTraceEvent: ...
    async def read_run(self, run_id: UUID) -> Sequence[AnyTraceEvent]: ...
