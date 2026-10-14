"""Event-sourced, model-authored experiment commitment; no oracle or root logic."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from pydantic import Field

from offsecgym.interfaces import EventStore
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import ExperimentStateChanged, RunCompleted
from offsecgym.storage.event_store import PostgresEventStore

StepText = Annotated[str, Field(min_length=1, max_length=240)]


class StartExperimentArgs(StrictModel):
    hypothesis: str = Field(min_length=1, max_length=512)
    subject_identity: UUID | None
    target_objects: tuple[UUID, ...] = Field(max_length=8)
    preconditions: tuple[StepText, ...] = Field(max_length=8)
    planned_steps: tuple[StepText, ...] = Field(min_length=1, max_length=12)
    expected_positive_observation: str = Field(min_length=1, max_length=512)
    expected_negative_observation: str = Field(min_length=1, max_length=512)
    required_evidence: tuple[StepText, ...] = Field(max_length=8)


class GetActiveExperimentArgs(StrictModel):
    pass


class UpdateExperimentArgs(StrictModel):
    experiment_id: UUID
    current_step: int = Field(ge=0)
    status: str = Field(pattern="^(active|blocked)$")
    blockers: tuple[StepText, ...] = Field(max_length=8)


class CompleteExperimentArgs(StrictModel):
    experiment_id: UUID


class AbandonExperimentArgs(StrictModel):
    experiment_id: UUID
    reason: str = Field(min_length=1, max_length=512)


EXPERIMENT_TOOL_MODELS: dict[str, type[StrictModel]] = {
    "start_experiment": StartExperimentArgs,
    "get_active_experiment": GetActiveExperimentArgs,
    "update_experiment": UpdateExperimentArgs,
    "complete_experiment": CompleteExperimentArgs,
    "abandon_experiment": AbandonExperimentArgs,
}


class ExperimentStateStore:
    def __init__(self, events: EventStore) -> None:
        self.events = events

    @staticmethod
    def _active(trace: list[object]) -> ExperimentStateChanged | None:
        latest: dict[UUID, ExperimentStateChanged] = {}
        for event in trace:
            if isinstance(event, ExperimentStateChanged):
                latest[event.experiment_id] = event
        active = [e for e in latest.values() if e.status in {"active", "blocked"}]
        if len(active) > 1:
            raise ValueError("multiple active experiments in run")
        return active[0] if active else None

    async def active(self, run_id: UUID) -> ExperimentStateChanged | None:
        return self._active(await self.events.read_run(run_id))

    async def change(
        self, run_id: UUID, name: str, args: StrictModel, *, causation_id: UUID
    ) -> dict[str, object]:
        if isinstance(self.events, PostgresEventStore):
            async with self.events.run_transaction(run_id) as tx:
                return await self._change(tx, run_id, name, args, causation_id)
        return await self._change(self.events, run_id, name, args, causation_id)

    async def _change(
        self, store: EventStore, run_id: UUID, name: str, args: StrictModel, causation_id: UUID
    ) -> dict[str, object]:
        trace = await store.read_run(run_id)
        if any(isinstance(e, RunCompleted) for e in trace):
            raise ValueError("experiment cannot change after terminal")
        current = self._active(trace)
        if name == "get_active_experiment":
            return self._view(current, trace)
        if name == "start_experiment":
            if current is not None:
                raise ValueError("active_experiment_exists")
            assert isinstance(args, StartExperimentArgs)
            event = ExperimentStateChanged(
                run_id=run_id,
                actor="agent",
                causation_id=causation_id,
                experiment_id=uuid4(),
                **args.model_dump(mode="python"),
            )
        else:
            if current is None or args.experiment_id != current.experiment_id:
                raise ValueError("active_experiment_not_found")
            fields = current.model_dump(
                mode="python",
                include={
                    "experiment_id",
                    "hypothesis",
                    "subject_identity",
                    "target_objects",
                    "preconditions",
                    "planned_steps",
                    "expected_positive_observation",
                    "expected_negative_observation",
                    "required_evidence",
                    "current_step",
                    "status",
                    "blockers",
                },
            )
            if name == "update_experiment":
                assert isinstance(args, UpdateExperimentArgs)
                if args.current_step < current.current_step:
                    raise ValueError("experiment_step_cannot_regress")
                if args.current_step > len(current.planned_steps):
                    raise ValueError("experiment_step_out_of_range")
                fields.update(
                    current_step=args.current_step, status=args.status, blockers=args.blockers
                )
            elif name == "complete_experiment":
                fields.update(status="completed", blockers=())
            elif name == "abandon_experiment":
                assert isinstance(args, AbandonExperimentArgs)
                fields.update(status="abandoned", blockers=(args.reason,))
            else:
                raise ValueError("unknown experiment tool")
            event = ExperimentStateChanged(
                run_id=run_id, actor="agent", causation_id=causation_id, **fields
            )
        persisted = await store.append(event)
        return self._view(persisted, [*trace, persisted])

    @staticmethod
    def _view(event: ExperimentStateChanged | None, trace: list[object]) -> dict[str, object]:
        if event is None:
            return {"active": False}
        first = next(
            x
            for x in trace
            if isinstance(x, ExperimentStateChanged) and x.experiment_id == event.experiment_id
        )
        return {
            "active": event.status in {"active", "blocked"},
            "experiment_id": str(event.experiment_id),
            "hypothesis": event.hypothesis,
            "subject_identity": str(event.subject_identity) if event.subject_identity else None,
            "target_objects": [str(x) for x in event.target_objects],
            "preconditions": list(event.preconditions),
            "planned_steps": list(event.planned_steps),
            "expected_positive_observation": event.expected_positive_observation,
            "expected_negative_observation": event.expected_negative_observation,
            "required_evidence": list(event.required_evidence),
            "current_step": event.current_step,
            "status": event.status,
            "blockers": list(event.blockers),
            "created_at_sequence": first.sequence_number,
            "updated_at_sequence": event.sequence_number,
        }

    async def reminder(self, run_id: UUID, *, max_chars: int = 900) -> str:
        current = await self.active(run_id)
        if current is None:
            return ""
        completed = current.planned_steps[: current.current_step]
        next_step = (
            current.planned_steps[current.current_step]
            if current.current_step < len(current.planned_steps)
            else "none"
        )
        text = (
            "Active experiment (agent-authored):\n"
            f"hypothesis: {current.hypothesis}\n"
            f"current step: {current.current_step}/{len(current.planned_steps)}\n"
            f"completed steps: {'; '.join(completed) or 'none'}\n"
            f"blocker: {'; '.join(current.blockers) or 'none'}\n"
            f"next planned step: {next_step}"
        )
        return text[:max_chars]
