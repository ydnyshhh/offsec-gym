"""Assigned missingness and independent M6.7 stage predicates."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from offsecgym.research import m67_stage_analysis
from offsecgym.research.m67_stage_analysis import (
    VerifiedStageSignals,
    _submitted_reference_metrics,
    analyze_run,
)
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    ExperimentStateChanged,
    ModelCallStarted,
    RunCompleted,
)


def _sequence(*events):
    return [
        event.model_copy(update={"sequence_number": i}) for i, event in enumerate(events, start=1)
    ]


def test_provider_failed_intact_trace_retains_experiment_stage_and_null_usage(tmp_path):
    run_id = uuid4()
    trace = _sequence(
        ExperimentStateChanged(
            run_id=run_id,
            actor="agent",
            experiment_id=uuid4(),
            hypothesis="A public rule may be inconsistent",
            planned_steps=("read", "test"),
            expected_positive_observation="state changes",
            expected_negative_observation="state unchanged",
        ),
        ModelCallStarted(
            schema_version="1",
            run_id=run_id,
            actor="controller",
            call_id=uuid4(),
            provider="fake",
            model="fake",
            input_sha256="a" * 64,
        ),
        RunCompleted(run_id=run_id, actor="controller", status="provider_failed"),
    )
    row = analyze_run(trace, tmp_path, uuid4(), signals=VerifiedStageSignals())
    assert row["trace_auditable"] is True
    assert row["score_valid"] is False
    assert row["experiments_started"] == 1
    assert row["target_transition"] is False
    assert row["resource_auditable"] is False
    assert row["model_calls"] == 1
    assert row["input_tokens"] is None and row["output_tokens"] is None


def test_corrupt_evidence_is_assigned_missing(tmp_path):
    run_id, action_id = uuid4(), uuid4()
    start = ActionRequested(
        schema_version="1",
        run_id=run_id,
        actor="gateway",
        action_id=action_id,
        action_type="http_request",
        destination="synthetic",
        method="GET",
    )
    trace = _sequence(
        start,
        ActionCompleted(
            run_id=run_id,
            actor="gateway",
            action_id=action_id,
            evidence_id=uuid4(),
            duration_ms=1,
            http_status=200,
            response_sha256="b" * 64,
            causation_id=start.event_id,
        ),
        RunCompleted(run_id=run_id, actor="controller", status="completed"),
    )
    row = analyze_run(trace, tmp_path, uuid4(), signals=VerifiedStageSignals())
    assert row["assigned"] is True
    assert row["trace_auditable"] is False
    assert row["model_calls"] is None
    assert "missing" in row["missing_reason"]


def test_unexpected_analyzer_error_is_not_experimental_missing(tmp_path, monkeypatch):
    run_id = uuid4()
    trace = _sequence(RunCompleted(run_id=run_id, actor="controller", status="completed"))

    def bug(*_args):
        raise TypeError("analyzer bug")

    monkeypatch.setattr(m67_stage_analysis, "_verify_actions", bug)
    with pytest.raises(TypeError, match="analyzer bug"):
        analyze_run(trace, tmp_path, uuid4(), signals=VerifiedStageSignals())


def test_post_terminal_event_is_assigned_missing_not_counted(tmp_path):
    run_id = uuid4()
    trace = _sequence(
        RunCompleted(run_id=run_id, actor="controller", status="completed"),
        ExperimentStateChanged(
            run_id=run_id,
            actor="agent",
            experiment_id=uuid4(),
            hypothesis="late",
            planned_steps=("test",),
            expected_positive_observation="yes",
            expected_negative_observation="no",
        ),
    )
    row = analyze_run(trace, tmp_path, uuid4(), signals=VerifiedStageSignals())
    assert row["trace_auditable"] is False
    assert row["model_calls"] is None


def test_submission_citations_are_audited_in_baseline_output():
    run_id, task_id, call_id = uuid4(), uuid4(), uuid4()
    action_a, action_b, evidence_a, evidence_b = uuid4(), uuid4(), uuid4(), uuid4()
    starts = [
        ActionRequested(
            schema_version="1",
            run_id=run_id,
            actor="gateway",
            action_id=action_id,
            action_type="http_request",
            destination="enterprise_change_control_v1",
            method="POST",
            task_id=task_id,
            originating_call_id=call_id,
            originating_tool_call_id=f"call-{index}",
        )
        for index, action_id in enumerate((action_a, action_b))
    ]
    trace = _sequence(
        *starts,
        ActionCompleted(
            run_id=run_id,
            actor="gateway",
            action_id=action_a,
            evidence_id=evidence_a,
            duration_ms=1,
            http_status=200,
            response_sha256="a" * 64,
        ),
        ActionCompleted(
            run_id=run_id,
            actor="gateway",
            action_id=action_b,
            evidence_id=evidence_b,
            duration_ms=1,
            http_status=200,
            response_sha256="b" * 64,
        ),
        RunCompleted(run_id=run_id, actor="controller", status="completed"),
    )
    calls = [
        {
            "name": "submit_finding",
            "arguments": json.dumps(
                {
                    "action": "approve",
                    "evidence": [
                        {"action_id": str(action_a), "evidence_id": str(evidence_b)},
                        {"action_id": str(uuid4()), "evidence_id": str(evidence_a)},
                        {"action_id": "bad", "evidence_id": str(evidence_a)},
                    ],
                }
            ),
        }
    ]
    metrics = _submitted_reference_metrics(calls, trace, trace[-1].sequence_number)
    assert metrics == {
        "invalid_action_evidence_pairs": 1,
        "same_run_reference_failures": 1,
        "malformed_citation_references": 1,
        "noncanonical_route_rejections": 1,
    }


def test_experiment_action_windows_do_not_absorb_search_between_experiments(tmp_path):
    run_id, first_id, second_id = uuid4(), uuid4(), uuid4()

    def experiment(experiment_id, status):
        return ExperimentStateChanged(
            run_id=run_id,
            actor="agent",
            experiment_id=experiment_id,
            hypothesis="A visible policy may be inconsistent",
            planned_steps=("read",),
            expected_positive_observation="changed",
            expected_negative_observation="unchanged",
            status=status,
        )

    def action():
        return ActionRequested(
            schema_version="1",
            run_id=run_id,
            actor="gateway",
            action_id=uuid4(),
            action_type="http_request",
            destination="enterprise_change_control_v1",
        )

    trace = _sequence(
        experiment(first_id, "active"),
        action(),
        experiment(first_id, "abandoned"),
        action(),
        experiment(second_id, "active"),
        action(),
        experiment(second_id, "completed"),
        RunCompleted(run_id=run_id, actor="controller", status="completed"),
    )
    row = m67_stage_analysis._analyze_verified(trace, tmp_path, VerifiedStageSignals())
    assert row["actions_per_experiment"] == [1, 1]
    assert row["tokens_per_experiment"] == [0, 0]
