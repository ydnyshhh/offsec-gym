"""No-model scripted qualification of the existing experiment-state store.

This module has no controller, provider, gateway, or range dependency. It does
not participate in the live agent's tool dispatch.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from offsecgym.interfaces import EventStore
from offsecgym.research.m681_reference_syntax import ReferenceSyntaxError, parse_reference
from offsecgym.schemas.events import ExperimentStateChanged, RunStarted
from offsecgym.solver.experiment_state import (
    AbandonExperimentArgs,
    CompleteExperimentArgs,
    ExperimentStateStore,
    GetActiveExperimentArgs,
    StartExperimentArgs,
    UpdateExperimentArgs,
)

OperationName = Literal["get", "update", "complete", "abandon"]


@dataclass(frozen=True)
class SyntheticRunContext:
    """Marker for a fabricated run; it is not a range or agent context."""

    experiment_hash: str = "m6811-detached-synthetic"


@dataclass(frozen=True)
class ScriptedOperation:
    name: OperationName
    current_step: int | None = None
    status: Literal["active", "blocked"] = "active"
    blockers: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class DetachedResult:
    run_id: UUID
    validated_arguments: StartExperimentArgs
    views: tuple[dict[str, object], ...]
    state_events: tuple[ExperimentStateChanged, ...]

    def scientific_projection(self) -> dict[str, object]:
        """Exclude generated IDs/timestamps; retain observed state transitions."""
        return {
            "validated_target_objects": [
                str(value) for value in self.validated_arguments.target_objects
            ],
            "planned_steps": list(self.validated_arguments.planned_steps),
            "views": [
                {
                    "active": view["active"],
                    "current_step": view.get("current_step"),
                    "status": view.get("status"),
                    "created_at_sequence": view.get("created_at_sequence"),
                    "updated_at_sequence": view.get("updated_at_sequence"),
                }
                for view in self.views
            ],
            "state_events": [
                {
                    "sequence_number": event.sequence_number,
                    "status": event.status,
                    "current_step": event.current_step,
                    "target_objects": [str(value) for value in event.target_objects],
                    "planned_steps": list(event.planned_steps),
                }
                for event in self.state_events
            ],
        }

    def event_integrity_sha256(self) -> tuple[str, ...]:
        """Hash real authoritative events without replacing ephemeral identities."""
        return tuple(
            hashlib.sha256(
                json.dumps(
                    event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            for event in self.state_events
        )


def validate_start_arguments(
    scripted_argument_dict: dict[str, object], *, normalization_enabled: bool
) -> StartExperimentArgs:
    """Validate a copy of scripted input before any event-store operation."""
    candidate = deepcopy(scripted_argument_dict)
    if normalization_enabled:
        targets = candidate.get("target_objects")
        if not isinstance(targets, list) or not targets:
            raise ReferenceSyntaxError("unsupported_reference_array")
        parsed = tuple(parse_reference(value) for value in targets)
        candidate["target_objects"] = [item.canonical_uuid for item in parsed]
    return StartExperimentArgs.model_validate(candidate)


async def run_detached_script(
    events: EventStore,
    *,
    run_id: UUID,
    synthetic_run_context: SyntheticRunContext,
    scripted_argument_dict: dict[str, object],
    normalization_enabled: bool,
    scripted_operations: tuple[ScriptedOperation, ...],
) -> DetachedResult:
    """Run one synthetic lifecycle through the unchanged authoritative store.

    Validation happens first, so malformed input causes no run or state event.
    Later operations use the experiment ID returned by the real state store.
    """
    start_args = validate_start_arguments(
        scripted_argument_dict, normalization_enabled=normalization_enabled
    )
    if await events.read_run(run_id):
        raise ValueError("detached run_id must be unused")
    await events.append(
        RunStarted(
            run_id=run_id,
            actor="qualification",
            experiment_hash=synthetic_run_context.experiment_hash,
        )
    )
    state = ExperimentStateStore(events)
    first = await state.change(run_id, "start_experiment", start_args, causation_id=uuid4())
    experiment_id = UUID(str(first["experiment_id"]))
    views: list[dict[str, object]] = [first]
    for operation in scripted_operations:
        if operation.name == "get":
            name = "get_active_experiment"
            args = GetActiveExperimentArgs()
        elif operation.name == "update":
            if operation.current_step is None:
                raise ValueError("update requires current_step")
            name = "update_experiment"
            args = UpdateExperimentArgs(
                experiment_id=experiment_id,
                current_step=operation.current_step,
                status=operation.status,
                blockers=operation.blockers,
            )
        elif operation.name == "complete":
            name = "complete_experiment"
            args = CompleteExperimentArgs(experiment_id=experiment_id)
        elif operation.name == "abandon":
            if operation.reason is None:
                raise ValueError("abandon requires reason")
            name = "abandon_experiment"
            args = AbandonExperimentArgs(experiment_id=experiment_id, reason=operation.reason)
        else:
            raise ValueError("unknown scripted operation")
        views.append(await state.change(run_id, name, args, causation_id=uuid4()))
    trace = await events.read_run(run_id)
    return DetachedResult(
        run_id=run_id,
        validated_arguments=start_args,
        views=tuple(views),
        state_events=tuple(event for event in trace if isinstance(event, ExperimentStateChanged)),
    )
