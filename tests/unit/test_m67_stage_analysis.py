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
from offsecgym.schemas.domain import (
    AuthorizationExpectation,
    CandidateFinding,
    EvidenceRef,
    ValidationResult,
)
from offsecgym.schemas.events import (
    ActionBlocked,
    ActionCompleted,
    ActionFailed,
    ActionRequested,
    ExperimentStateChanged,
    FindingSubmitted,
    FindingValidated,
    ModelCallStarted,
    ModelToolRejected,
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


def _finding(run_id, finding_id):
    return CandidateFinding(
        finding_id=finding_id,
        run_id=run_id,
        range_instance_id=uuid4(),
        range_generation=0,
        claim="A model-authored claim",
        family="enterprise_change_control_v1",
        asset_id=uuid4(),
        security_property=AuthorizationExpectation(
            subject_role="reviewer",
            action="POST /api/changes/{id}/approve",
            resource_type="change_request",
            object_relation="other",
            expected="deny",
        ),
        evidence=(EvidenceRef(evidence_id=uuid4(), action_id=uuid4(), description="observed"),),
    )


@pytest.mark.parametrize("root_later", [False, True])
@pytest.mark.parametrize("validated", [False, True])
def test_transition_reporting_requires_same_root_finding_id(tmp_path, root_later, validated):
    run_id, action_id, finding_id = uuid4(), uuid4(), uuid4()
    events = [
        ActionCompleted(run_id=run_id, actor="gateway", action_id=action_id, duration_ms=1),
        FindingSubmitted(run_id=run_id, actor="solver", finding=_finding(run_id, finding_id)),
    ]
    if validated:
        events.append(
            FindingValidated(
                run_id=run_id,
                actor="validator",
                result=ValidationResult(run_id=run_id, finding_id=finding_id, status="validated"),
            )
        )
    events.append(RunCompleted(run_id=run_id, actor="controller", status="completed"))
    signals = VerifiedStageSignals(
        transition_action_ids=frozenset({action_id}),
        root_class_finding_ids=frozenset({finding_id}) if root_later else frozenset(),
    )
    row = m67_stage_analysis._analyze_verified(_sequence(*events), tmp_path, signals)
    assert row["any_finding_after_transition"] is True
    assert row["root_recorded_finding_after_transition"] is root_later
    assert row["any_validated_finding_after_transition"] is validated
    assert row["root_validated_finding_after_transition"] is (root_later and validated)
    assert row["root_submission_attempt_after_transition"] is None


def test_malformed_submit_cannot_be_inferred_as_root_attempt(tmp_path):
    run_id, action_id = uuid4(), uuid4()
    trace = _sequence(
        ActionCompleted(run_id=run_id, actor="gateway", action_id=action_id, duration_ms=1),
        ModelToolRejected(
            run_id=run_id,
            actor="controller",
            model_call_id=uuid4(),
            tool_call_id="submit-1",
            tool_name="submit_enterprise_finding",
            reason_code="invalid_tool_json",
        ),
        RunCompleted(run_id=run_id, actor="controller", status="completed"),
    )
    row = m67_stage_analysis._analyze_verified(
        trace, tmp_path, VerifiedStageSignals(transition_action_ids=frozenset({action_id}))
    )
    assert row["any_submission_attempt_after_transition"] is True
    assert row["root_submission_attempt_after_transition"] is None
    assert row["root_recorded_finding_after_transition"] is False


def test_root_verdict_requires_same_later_root_finding(tmp_path):
    run_id, transition_id = uuid4(), uuid4()
    root_before, root_after, unrelated = uuid4(), uuid4(), uuid4()
    trace = _sequence(
        FindingSubmitted(run_id=run_id, actor="solver", finding=_finding(run_id, root_before)),
        ActionCompleted(run_id=run_id, actor="gateway", action_id=transition_id, duration_ms=1),
        FindingSubmitted(run_id=run_id, actor="solver", finding=_finding(run_id, root_after)),
        FindingSubmitted(run_id=run_id, actor="solver", finding=_finding(run_id, unrelated)),
        FindingValidated(
            run_id=run_id,
            actor="validator",
            result=ValidationResult(run_id=run_id, finding_id=unrelated, status="validated"),
        ),
        FindingValidated(
            run_id=run_id,
            actor="validator",
            result=ValidationResult(run_id=run_id, finding_id=root_before, status="validated"),
        ),
        RunCompleted(run_id=run_id, actor="controller", status="completed"),
    )
    signals = VerifiedStageSignals(
        transition_action_ids=frozenset({transition_id}),
        root_class_finding_ids=frozenset({root_before, root_after}),
    )
    row = m67_stage_analysis._analyze_verified(trace, tmp_path, signals)
    assert row["any_validated_finding_after_transition"] is True
    assert row["root_recorded_finding_after_transition"] is True
    assert row["root_validated_finding_after_transition"] is False


@pytest.mark.parametrize("outcome", ["none", "failed", "blocked", "completed", "transition"])
def test_decisive_request_completion_and_transition_are_independent(tmp_path, outcome):
    run_id, action_id = uuid4(), uuid4()
    events = []
    if outcome != "none":
        events.append(
            ActionRequested(
                schema_version="1",
                run_id=run_id,
                actor="gateway",
                action_id=action_id,
                action_type="http_request",
                destination="enterprise_change_control_v1",
            )
        )
    if outcome == "failed":
        events.append(
            ActionFailed(run_id=run_id, actor="gateway", action_id=action_id, reason_code="failed")
        )
    if outcome == "blocked":
        events.append(
            ActionBlocked(
                run_id=run_id, actor="gateway", action_id=action_id, reason_code="blocked"
            )
        )
    if outcome in {"completed", "transition"}:
        events.append(
            ActionCompleted(run_id=run_id, actor="gateway", action_id=action_id, duration_ms=1)
        )
    events.append(RunCompleted(run_id=run_id, actor="controller", status="completed"))
    signals = VerifiedStageSignals(
        decisive_action_request_ids=frozenset({action_id}) if outcome != "none" else frozenset(),
        decisive_action_completed_ids=(
            frozenset({action_id}) if outcome in {"completed", "transition"} else frozenset()
        ),
        transition_action_ids=frozenset({action_id}) if outcome == "transition" else frozenset(),
    )
    row = m67_stage_analysis._analyze_verified(_sequence(*events), tmp_path, signals)
    assert row["decisive_root_action_attempted"] is (outcome != "none")
    assert row["decisive_root_action_completed"] is (outcome in {"completed", "transition"})
    assert row["target_transition"] is (outcome == "transition")


def test_model_marked_completion_does_not_imply_verified_success(tmp_path):
    run_id = uuid4()
    trace = _sequence(
        ExperimentStateChanged(
            run_id=run_id,
            actor="agent",
            experiment_id=uuid4(),
            hypothesis="Test a boundary",
            planned_steps=("read",),
            current_step=1,
            status="completed",
            expected_positive_observation="changed",
            expected_negative_observation="unchanged",
        ),
        RunCompleted(run_id=run_id, actor="controller", status="completed"),
    )
    row = m67_stage_analysis._analyze_verified(trace, tmp_path, VerifiedStageSignals())
    assert row["model_marked_experiments_completed"] == 1
    assert row["model_reported_completed_steps"] == 1
    assert not row["decisive_root_action_completed"]
    assert not row["target_transition"]
    assert not row["complete_witness"]
    assert not row["root_validated_finding_after_transition"]
