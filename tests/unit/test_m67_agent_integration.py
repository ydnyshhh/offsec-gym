"""Scripted provider qualification; zero external model calls."""

from __future__ import annotations

import json

import pytest
from test_milestone_3_runner import MemoryEvents, no_docker_runtime
from test_milestone_4 import QueueProvider, model_spec, turn

from offsecgym.experiment.m67 import M67Arm, M67MonolithicExperimentRunner


def _experiment_call():
    return {
        "type": "function_call",
        "call_id": "start-1",
        "name": "start_experiment",
        "arguments": json.dumps(
            {
                "hypothesis": "An observed policy may be inconsistent",
                "subject_identity": None,
                "target_objects": [],
                "preconditions": ["read state"],
                "planned_steps": ["read", "test"],
                "expected_positive_observation": "state changes",
                "expected_negative_observation": "state does not change",
                "required_evidence": ["response"],
            }
        ),
    }


@pytest.mark.asyncio
async def test_structured_rebuild_has_model_authored_reminder(tmp_path, monkeypatch):
    provider = QueueProvider(turn(_experiment_call()), turn())
    spec = model_spec(calls=2).model_copy(update={"memory": "structured"})
    await M67MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch),
        MemoryEvents(),
        provider,
        arm=M67Arm(provenance_reporting=False, experiment_commitment=True),
    ).run(spec)
    assert len(provider.requests) == 2
    first, second = provider.requests
    names = {tool["name"] for tool in first["tools"]}
    assert "start_experiment" in names
    assert "preflight_finding" not in names
    joined = json.dumps(second["input"])
    assert "An observed policy may be inconsistent" in joined
    assert "next planned step: read" in joined
    assert "B2-REVOKED-ROLE" not in joined
    assert "B3-CANCELLED-JOB" not in joined


@pytest.mark.asyncio
async def test_baseline_agent_has_no_intervention_tool_or_reminder(tmp_path, monkeypatch):
    provider = QueueProvider(turn())
    spec = model_spec(calls=1).model_copy(update={"memory": "structured"})
    await M67MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch),
        MemoryEvents(),
        provider,
        arm=M67Arm(provenance_reporting=False, experiment_commitment=False),
    ).run(spec)
    request = provider.requests[0]
    names = {tool["name"] for tool in request["tools"]}
    assert "start_experiment" not in names
    assert "list_candidate_evidence" not in names
    assert "Active experiment" not in json.dumps(request["input"])
