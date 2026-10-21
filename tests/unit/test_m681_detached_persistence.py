"""Synthetic, no-model checks of the detached experiment-state boundary."""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from offsecgym.research.m681_detached_persistence import (
    ScriptedOperation,
    SyntheticRunContext,
    run_detached_script,
    validate_start_arguments,
)
from offsecgym.research.m681_reference_syntax import ReferenceSyntaxError
from offsecgym.schemas.events import ExperimentStateChanged, RunCompleted, RunStarted
from offsecgym.solver.experiment_state import (
    ExperimentStateStore,
    GetActiveExperimentArgs,
    StartExperimentArgs,
    UpdateExperimentArgs,
)

OBJECT = UUID("11111111-1111-4111-8111-111111111111")
SECOND = UUID("22222222-2222-4222-8222-222222222222")


class MemoryEvents:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def append(self, event):
        sequence = len(await self.read_run(event.run_id)) + 1
        persisted = event.model_copy(update={"sequence_number": sequence})
        self.events.append(persisted)
        return persisted

    async def read_run(self, run_id):
        return [event for event in self.events if event.run_id == run_id]


def arguments(*, targets: list[str] | None = None) -> dict[str, object]:
    return {
        "hypothesis": "Synthetic state remains observable",
        "subject_identity": None,
        "target_objects": targets if targets is not None else [f"project:{OBJECT}"],
        "preconditions": ["Read synthetic baseline"],
        "planned_steps": ["Observe", "Record", "Compare"],
        "expected_positive_observation": "State changed",
        "expected_negative_observation": "State unchanged",
        "required_evidence": ["Synthetic note"],
    }


@pytest.mark.parametrize(
    ("targets", "enabled", "expected"),
    [
        ([str(OBJECT)], False, (OBJECT,)),
        ([f"project:{OBJECT}"], True, (OBJECT,)),
        ([str(OBJECT)], True, (OBJECT,)),
        ([f"project:{OBJECT}", f"access_request:{SECOND}"], True, (OBJECT, SECOND)),
    ],
)
def test_validated_references_preserve_uuid_and_order(targets, enabled, expected):
    original = arguments(targets=targets)
    copy = deepcopy(original)
    validated = validate_start_arguments(original, normalization_enabled=enabled)
    assert validated.target_objects == expected
    assert original == copy


def test_strict_mode_preserves_original_error():
    raw = arguments()
    with pytest.raises(ValidationError) as original:
        StartExperimentArgs.model_validate(raw)
    with pytest.raises(ValidationError) as detached:
        validate_start_arguments(raw, normalization_enabled=False)
    assert original.value.errors() == detached.value.errors()


@pytest.mark.parametrize(
    "targets",
    [
        [f"change:{OBJECT}"],
        [f"project {OBJECT}"],
        [f"project:{OBJECT} trailing"],
        [f"project:{OBJECT} {SECOND}"],
        ["change workflow endpoints"],
        [f"project:{OBJECT}", f"change:{SECOND}"],
        [],
    ],
)
@pytest.mark.asyncio
async def test_rejected_references_produce_no_events(targets):
    events = MemoryEvents()
    run_id = uuid4()
    with pytest.raises(ReferenceSyntaxError):
        await run_detached_script(
            events,
            run_id=run_id,
            synthetic_run_context=SyntheticRunContext(),
            scripted_argument_dict=arguments(targets=targets),
            normalization_enabled=True,
            scripted_operations=(),
        )
    assert await events.read_run(run_id) == []


@pytest.mark.asyncio
async def test_invalid_other_schema_field_produces_no_events():
    events = MemoryEvents()
    raw = arguments()
    raw["planned_steps"] = []
    with pytest.raises(ValidationError):
        await run_detached_script(
            events,
            run_id=uuid4(),
            synthetic_run_context=SyntheticRunContext(),
            scripted_argument_dict=raw,
            normalization_enabled=True,
            scripted_operations=(),
        )
    assert events.events == []


@pytest.mark.asyncio
async def test_scripted_lifecycle_uses_authoritative_events_and_is_deterministic():
    async def once():
        events = MemoryEvents()
        run_id = uuid4()
        result = await run_detached_script(
            events,
            run_id=run_id,
            synthetic_run_context=SyntheticRunContext(),
            scripted_argument_dict=arguments(),
            normalization_enabled=True,
            scripted_operations=(
                ScriptedOperation("get"),
                ScriptedOperation("update", current_step=0),
                ScriptedOperation("update", current_step=1, status="blocked", blockers=("Wait",)),
                ScriptedOperation("get"),
                ScriptedOperation("update", current_step=2),
                ScriptedOperation("update", current_step=3),
                ScriptedOperation("complete"),
                ScriptedOperation("get"),
            ),
        )
        trace = await events.read_run(run_id)
        assert isinstance(trace[0], RunStarted)
        assert result.state_events == tuple(
            event for event in trace if isinstance(event, ExperimentStateChanged)
        )
        assert [event.sequence_number for event in trace] == list(range(1, len(trace) + 1))
        assert {event.run_id for event in trace} == {run_id}
        assert {event.experiment_id for event in result.state_events} == {
            UUID(str(result.views[0]["experiment_id"]))
        }
        assert all(event.target_objects == (OBJECT,) for event in result.state_events)
        assert all(
            event.planned_steps == ("Observe", "Record", "Compare") for event in result.state_events
        )
        assert result.state_events[-1].status == "completed"
        assert result.views[-1] == {"active": False}
        assert len(result.event_integrity_sha256()) == len(result.state_events)
        return result

    first, second = await once(), await once()
    assert first.scientific_projection() == second.scientific_projection()
    assert first.event_integrity_sha256() != second.event_integrity_sha256()


@pytest.mark.asyncio
async def test_abandonment_and_cross_run_isolation():
    events = MemoryEvents()
    run_a, run_b = uuid4(), uuid4()
    result = await run_detached_script(
        events,
        run_id=run_a,
        synthetic_run_context=SyntheticRunContext(),
        scripted_argument_dict=arguments(),
        normalization_enabled=True,
        scripted_operations=(ScriptedOperation("abandon", reason="Synthetic stop"),),
    )
    assert result.state_events[-1].status == "abandoned"
    assert await ExperimentStateStore(events).active(run_b) is None
    assert await events.read_run(run_b) == []
    with pytest.raises(ValueError, match="active_experiment_not_found"):
        await ExperimentStateStore(events).change(
            run_b,
            "update_experiment",
            UpdateExperimentArgs(
                experiment_id=result.state_events[-1].experiment_id,
                current_step=1,
                status="active",
                blockers=(),
            ),
            causation_id=uuid4(),
        )
    assert await events.read_run(run_b) == []


@pytest.mark.asyncio
async def test_terminal_duplicate_and_invalid_progress_leave_no_new_state_event():
    events = MemoryEvents()
    run_id = uuid4()
    result = await run_detached_script(
        events,
        run_id=run_id,
        synthetic_run_context=SyntheticRunContext(),
        scripted_argument_dict=arguments(),
        normalization_enabled=True,
        scripted_operations=(),
    )
    state = ExperimentStateStore(events)
    experiment_id = result.state_events[-1].experiment_id
    prior = len(await events.read_run(run_id))
    with pytest.raises(ValueError, match="active_experiment_exists"):
        await state.change(
            run_id, "start_experiment", result.validated_arguments, causation_id=uuid4()
        )
    for step in (999,):
        with pytest.raises(ValueError, match="experiment_step_out_of_range"):
            await state.change(
                run_id,
                "update_experiment",
                UpdateExperimentArgs(
                    experiment_id=experiment_id, current_step=step, status="active", blockers=()
                ),
                causation_id=uuid4(),
            )
    with pytest.raises(ValidationError):
        UpdateExperimentArgs(
            experiment_id=experiment_id, current_step=-1, status="active", blockers=()
        )
    with pytest.raises(ValidationError):
        UpdateExperimentArgs(
            experiment_id=experiment_id, current_step="not-an-int", status="active", blockers=()
        )
    assert len(await events.read_run(run_id)) == prior
    await state.change(
        run_id,
        "update_experiment",
        UpdateExperimentArgs(
            experiment_id=experiment_id, current_step=2, status="active", blockers=()
        ),
        causation_id=uuid4(),
    )
    before_regression = len(await events.read_run(run_id))
    with pytest.raises(ValueError, match="experiment_step_cannot_regress"):
        await state.change(
            run_id,
            "update_experiment",
            UpdateExperimentArgs(
                experiment_id=experiment_id, current_step=1, status="active", blockers=()
            ),
            causation_id=uuid4(),
        )
    assert len(await events.read_run(run_id)) == before_regression
    await events.append(RunCompleted(run_id=run_id, actor="qualification", status="completed"))
    before_terminal = len(await events.read_run(run_id))
    for name, args in (
        ("start_experiment", result.validated_arguments),
        (
            "update_experiment",
            UpdateExperimentArgs(
                experiment_id=experiment_id, current_step=3, status="active", blockers=()
            ),
        ),
        ("get_active_experiment", GetActiveExperimentArgs()),
    ):
        with pytest.raises(ValueError, match="experiment cannot change after terminal"):
            await state.change(run_id, name, args, causation_id=uuid4())
    assert len(await events.read_run(run_id)) == before_terminal
