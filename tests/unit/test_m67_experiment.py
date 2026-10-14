"""Opt-in model-authored experiment state and factorial arm isolation."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from test_milestone_4 import model_spec

from offsecgym.experiment.m67 import M67Arm
from offsecgym.solver.experiment_state import (
    AbandonExperimentArgs,
    CompleteExperimentArgs,
    ExperimentStateStore,
    StartExperimentArgs,
    UpdateExperimentArgs,
)
from offsecgym.solver.monolithic import model_tools

FAMILY = "enterprise_change_control_v1"


class MemoryEvents:
    def __init__(self):
        self.events = []

    async def append(self, event):
        item = event.model_copy(update={"sequence_number": len(self.events) + 1})
        self.events.append(item)
        return item

    async def read_run(self, run_id):
        return [event for event in self.events if event.run_id == run_id]


def _start(hypothesis="An authorization rule may be inconsistent"):
    return StartExperimentArgs(
        hypothesis=hypothesis,
        subject_identity=None,
        target_objects=(),
        preconditions=("observe state",),
        planned_steps=("read", "test", "read again"),
        expected_positive_observation="state changes",
        expected_negative_observation="state stays unchanged",
        required_evidence=("response",),
    )


@pytest.mark.asyncio
async def test_experiment_lifecycle_persists_without_root_injection():
    events = MemoryEvents()
    run_id, cause = uuid4(), uuid4()
    state = ExperimentStateStore(events)
    first = await state.change(run_id, "start_experiment", _start(), causation_id=cause)
    experiment_id = first["experiment_id"]
    assert first["active"]
    with pytest.raises(ValueError, match="active_experiment_exists"):
        await state.change(run_id, "start_experiment", _start("second"), causation_id=cause)
    rebuilt = ExperimentStateStore(events)
    reminder = await rebuilt.reminder(run_id)
    assert "An authorization rule may be inconsistent" in reminder
    assert "B2" not in reminder and "B3" not in reminder
    assert "cancel this job" not in reminder
    assert "next planned step: read" in reminder
    await rebuilt.change(
        run_id,
        "update_experiment",
        UpdateExperimentArgs(
            experiment_id=experiment_id,
            current_step=1,
            status="blocked",
            blockers=("need another observation",),
        ),
        causation_id=cause,
    )
    assert "next planned step: test" in await state.reminder(run_id)
    await state.change(
        run_id,
        "abandon_experiment",
        AbandonExperimentArgs(
            experiment_id=experiment_id,
            reason="evidence contradicted hypothesis",
        ),
        causation_id=cause,
    )
    assert await state.reminder(run_id) == ""
    second = await state.change(
        run_id, "start_experiment", _start("new hypothesis"), causation_id=cause
    )
    await state.change(
        run_id,
        "complete_experiment",
        CompleteExperimentArgs(
            experiment_id=second["experiment_id"],
        ),
        causation_id=cause,
    )
    assert await state.active(run_id) is None


@pytest.mark.asyncio
async def test_update_step_is_model_declared_completed_count_bounded_by_plan():
    events = MemoryEvents()
    run_id, cause = uuid4(), uuid4()
    state = ExperimentStateStore(events)
    started = await state.change(run_id, "start_experiment", _start(), causation_id=cause)
    experiment_id = started["experiment_id"]
    with pytest.raises(ValueError, match="experiment_step_out_of_range"):
        await state.change(
            run_id,
            "update_experiment",
            UpdateExperimentArgs(
                experiment_id=experiment_id, current_step=999, status="active", blockers=()
            ),
            causation_id=cause,
        )
    assert (await state.active(run_id)).current_step == 0
    assert "current step: 0/3" in await state.reminder(run_id)
    completed_plan = await state.change(
        run_id,
        "update_experiment",
        UpdateExperimentArgs(
            experiment_id=experiment_id, current_step=3, status="active", blockers=()
        ),
        causation_id=cause,
    )
    assert completed_plan["current_step"] == 3
    assert "current step: 3/3" in await state.reminder(run_id)
    assert "next planned step: none" in await state.reminder(run_id)
    with pytest.raises(ValueError, match="experiment_step_cannot_regress"):
        await state.change(
            run_id,
            "update_experiment",
            UpdateExperimentArgs(
                experiment_id=experiment_id, current_step=2, status="active", blockers=()
            ),
            causation_id=cause,
        )


def test_four_arms_are_explicit_and_schema_separated():
    configs = {
        name: yaml.safe_load(
            (Path(__file__).parents[2] / "experiments" / "configs" / f"m67-{name}.yaml").read_text()
        )
        for name in ("baseline", "reporting", "commitment", "reporting-commitment")
    }
    arms = {name: M67Arm.model_validate(config) for name, config in configs.items()}
    assert {name: arm.name for name, arm in arms.items()} == {
        "baseline": "A0B0",
        "reporting": "A1B0",
        "commitment": "A0B1",
        "reporting-commitment": "A1B1",
    }
    names = {
        name: {
            tool["name"]
            for tool in model_tools(
                family=FAMILY,
                structured=True,
                provenance_reporting=arm.provenance_reporting,
                experiment_commitment=arm.experiment_commitment,
            )
        }
        for name, arm in arms.items()
    }
    reporting = {"list_candidate_evidence", "preflight_finding"}
    commitment = {
        "start_experiment",
        "get_active_experiment",
        "update_experiment",
        "complete_experiment",
        "abandon_experiment",
    }
    assert names["reporting"] - names["baseline"] == reporting
    assert names["commitment"] - names["baseline"] == commitment
    assert names["reporting-commitment"] == names["baseline"] | reporting | commitment
    hashes = {name: arm.schema_sha256(family=FAMILY, structured=True) for name, arm in arms.items()}
    assert len(set(hashes.values())) == 4
    assert all(len(value) == 64 for value in hashes.values())
    spec = model_spec(calls=1).model_copy(update={"memory": "structured"})
    run_hashes = {name: arm.run_hash(spec) for name, arm in arms.items()}
    assert len(set(run_hashes.values())) == 4
    assert arms["baseline"].run_hash(spec) == run_hashes["baseline"]
    assert "B2-REVOKED-ROLE" not in json.dumps({k: sorted(v) for k, v in names.items()})
