"""Provider-independent analysis of retained, authoritative model-run events."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from statistics import mean, median
from typing import Literal
from uuid import UUID

from pydantic import Field

from offsecgym.benchmark.a1_scoring import reconstruct_score, score_agrees
from offsecgym.evaluation import RunEvaluation
from offsecgym.research.pilot.provenance import ProvenanceAudit
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallStarted,
    RangeStarted,
    RunCompleted,
    RunStarted,
    TraceEvent,
)
from offsecgym.schemas.ground_truth import GroundTruthManifest

ANALYSIS_VERSION = "r0-offline-analysis-v1"


class NormalizedTraceStep(StrictModel):
    sequence_number: int = Field(gt=0)
    event_type: str
    action_id: UUID | None = None
    evidence_id: UUID | None = None
    method: str | None = None
    route_sha256: str | None = None
    identity_id: UUID | None = None
    status: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    elapsed_ms: int | None = None


class OfflineRunRecord(StrictModel):
    analysis_version: Literal["r0-offline-analysis-v1"] = ANALYSIS_VERSION
    run_id: UUID
    run_status: str
    score_valid: bool | None
    score_replay_agrees: bool | None
    root_success: bool | None
    actions: int
    http_requests: int
    model_calls: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    wall_seconds: float | None
    repeated_requests: int
    exact_duplicate_requests: int
    distinct_route_hashes: int
    distinct_identities: int
    evidence_artifacts: int
    evidence_artifacts_cited: int
    invalid_evidence_citations: int
    findings_without_provenance: int
    evidence_reused_across_findings: int
    finding_submissions: int
    validated_findings: int
    rejected_findings: int
    inconclusive_findings: int
    first_finding_submission_step: int | None
    distinct_observed_objects: None = None
    repeated_unchanged_responses: int
    first_relevant_observation_step: None = None
    stages: dict[str, Literal["true", "unknown"]]
    integrity_issues: tuple[str, ...]
    trace_sha256: str
    provenance_audit: ProvenanceAudit | None = None


def normalize_events(events: tuple[TraceEvent, ...]) -> tuple[NormalizedTraceStep, ...]:
    return tuple(
        NormalizedTraceStep(
            sequence_number=event.sequence_number,
            event_type=event.type,
            action_id=getattr(event, "action_id", None),
            evidence_id=getattr(event, "evidence_id", None),
            method=event.method if isinstance(event, ActionRequested) else None,
            route_sha256=event.path_sha256 if isinstance(event, ActionRequested) else None,
            identity_id=event.identity_id if isinstance(event, ActionRequested) else None,
            status=(
                event.status
                if isinstance(event, RunCompleted)
                else event.result.status
                if isinstance(event, FindingValidated)
                else None
            ),
            input_tokens=event.input_tokens if isinstance(event, ModelCallCompleted) else None,
            output_tokens=event.output_tokens if isinstance(event, ModelCallCompleted) else None,
            elapsed_ms=event.duration_ms if isinstance(event, ActionCompleted) else None,
        )
        for event in events
    )


def analyze_run(
    events: tuple[TraceEvent, ...],
    *,
    evaluation: RunEvaluation | None = None,
    expected_root_cause_id: UUID | None = None,
    oracle: GroundTruthManifest | None = None,
) -> OfflineRunRecord:
    """Never infer success from a finding, HTTP response, or reference trace."""
    if not events:
        raise ValueError("a run requires retained events")
    run_id = events[0].run_id
    if any(row.run_id != run_id for row in events):
        raise ValueError("mixed run IDs")
    sequences = [row.sequence_number for row in events]
    if any(number <= 0 for number in sequences) or sequences != sorted(set(sequences)):
        raise ValueError("events must retain unique, ascending authoritative sequence numbers")
    starts = [row for row in events if isinstance(row, RunStarted)]
    endings = [row for row in events if isinstance(row, RunCompleted)]
    if (
        len(starts) != 1
        or len(endings) != 1
        or starts[0].sequence_number >= endings[0].sequence_number
    ):
        raise ValueError("retained run requires one ordered start and completion")
    requested = [row for row in events if isinstance(row, ActionRequested)]
    completed = [row for row in events if isinstance(row, ActionCompleted)]
    calls_started = [row for row in events if isinstance(row, ModelCallStarted)]
    calls_completed = [row for row in events if isinstance(row, ModelCallCompleted)]
    submissions = [row for row in events if isinstance(row, FindingSubmitted)]
    verdicts = [row for row in events if isinstance(row, FindingValidated)]
    issues: list[str] = []
    request_counts = Counter(row.action_id for row in requested)
    complete_counts = Counter(row.action_id for row in completed)
    if any(count != 1 for count in request_counts.values()):
        issues.append("duplicate_action_request")
    if any(count != 1 for count in complete_counts.values()):
        issues.append("duplicate_action_completion")
    if any(row.action_id not in request_counts for row in completed):
        issues.append("completion_without_request")
    if any(
        row.action_id in complete_counts
        and row.sequence_number
        >= next(item.sequence_number for item in completed if item.action_id == row.action_id)
        for row in requested
    ):
        issues.append("action_order")
    if any(
        row.call_id not in {start.call_id for start in calls_started} for row in calls_completed
    ):
        issues.append("model_completion_without_start")
    if len({row.event_id for row in events}) != len(events):
        issues.append("duplicate_event_id")
    if evaluation is not None and evaluation.status != endings[0].status:
        raise ValueError("canonical evaluation status disagrees with trace")
    replay_agrees = None
    if oracle is not None:
        if evaluation is None:
            raise ValueError("score replay requires a canonical evaluation")
        replay_agrees = score_agrees(
            reconstruct_score(
                run_id=run_id,
                events=events,
                oracle=oracle,
                status=endings[0].status,
            ),
            evaluation,
        )
        if not replay_agrees:
            raise ValueError("independent score replay disagrees with canonical evaluation")
    exact_keys = [
        (row.method, row.path_sha256, row.identity_id, row.body_sha256) for row in requested
    ]
    route_keys = [(row.method, row.path_sha256) for row in requested]
    request_by_action = {row.action_id: row for row in requested}
    paired_evidence = {
        (row.action_id, row.evidence_id)
        for row in completed
        if row.evidence_id is not None and row.action_id in request_by_action
    }
    citations = [ref for row in submissions for ref in row.finding.evidence]
    valid_citations = [
        ref for ref in citations if (ref.action_id, ref.evidence_id) in paired_evidence
    ]
    citation_counts = Counter(ref.evidence_id for ref in valid_citations)
    no_provenance = sum(
        not all((ref.action_id, ref.evidence_id) in paired_evidence for ref in row.finding.evidence)
        for row in submissions
    )
    repeated_response_count = 0
    observed_response_by_request: dict[tuple[object, ...], str] = {}
    for row in completed:
        request = request_by_action.get(row.action_id)
        if request is None or row.response_sha256 is None:
            continue
        key = (request.method, request.path_sha256, request.identity_id, request.body_sha256)
        if observed_response_by_request.get(key) == row.response_sha256:
            repeated_response_count += 1
        observed_response_by_request[key] = row.response_sha256
    matched = bool(
        expected_root_cause_id is not None
        and evaluation is not None
        and evaluation.score_valid
        and expected_root_cause_id in evaluation.matched_root_cause_ids
        and any(
            verdict.result.status == "validated"
            and verdict.result.matched_root_cause_id == expected_root_cause_id
            and any(
                submitted.finding.finding_id == verdict.result.finding_id
                and all(
                    (ref.action_id, ref.evidence_id) in paired_evidence
                    for ref in submitted.finding.evidence
                )
                for submitted in submissions
            )
            for verdict in verdicts
        )
    )
    costs = [row.estimated_cost_usd for row in calls_completed]
    raw = b"\n".join(
        json.dumps(row.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        for row in events
    )
    return OfflineRunRecord(
        run_id=run_id,
        run_status=endings[0].status,
        score_valid=evaluation.score_valid if evaluation else None,
        score_replay_agrees=replay_agrees,
        root_success=(
            expected_root_cause_id in evaluation.matched_root_cause_ids
            if expected_root_cause_id is not None
            and evaluation is not None
            and evaluation.score_valid
            else None
        ),
        actions=len(requested),
        http_requests=sum(row.action_type == "http_request" for row in requested),
        model_calls=len(calls_started),
        input_tokens=sum(row.input_tokens for row in calls_completed)
        if len(calls_completed) == len(calls_started)
        else None,
        output_tokens=sum(row.output_tokens for row in calls_completed)
        if len(calls_completed) == len(calls_started)
        else None,
        estimated_cost_usd=sum(costs)
        if costs and len(costs) == len(calls_started) and all(cost is not None for cost in costs)
        else None,
        wall_seconds=(endings[0].occurred_at - starts[0].occurred_at).total_seconds(),
        repeated_requests=len(route_keys) - len(set(route_keys)),
        exact_duplicate_requests=len(exact_keys) - len(set(exact_keys)),
        distinct_route_hashes=len(set(route_keys)),
        distinct_identities=len({row.identity_id for row in requested if row.identity_id}),
        evidence_artifacts=len({row.evidence_id for row in completed if row.evidence_id}),
        evidence_artifacts_cited=len(citation_counts),
        invalid_evidence_citations=len(citations) - len(valid_citations),
        findings_without_provenance=no_provenance,
        evidence_reused_across_findings=sum(count > 1 for count in citation_counts.values()),
        finding_submissions=len(submissions),
        validated_findings=sum(row.result.status == "validated" for row in verdicts),
        rejected_findings=sum(row.result.status == "rejected" for row in verdicts),
        inconclusive_findings=sum(row.result.status == "inconclusive" for row in verdicts),
        first_finding_submission_step=submissions[0].sequence_number if submissions else None,
        repeated_unchanged_responses=repeated_response_count,
        stages={
            f"S{index}": "true"
            if (index == 0 and any(isinstance(row, RangeStarted) for row in events))
            or (index in {5, 6} and matched)
            else "unknown"
            for index in range(7)
        },
        integrity_issues=tuple(issues),
        trace_sha256=hashlib.sha256(raw).hexdigest(),
    )


def empty_report() -> dict[str, object]:
    return {
        "analysis_version": ANALYSIS_VERSION,
        "status": "not_executed",
        "actual_model_runs": 0,
        "development_pilot": True,
        "held_out_evaluation": False,
        "model_performance_claims": False,
        "models": [],
        "paired_comparisons": [],
    }


def summarize(records: tuple[OfflineRunRecord, ...]) -> dict[str, object]:
    if not records:
        return empty_report()
    return {
        "analysis_version": ANALYSIS_VERSION,
        "status": "analyzed_existing",
        "actual_model_runs": len(records),
        "development_pilot": True,
        "held_out_evaluation": False,
        "model_performance_claims": False,
        "mean_actions": mean(row.actions for row in records),
        "median_actions": median(row.actions for row in records),
        "run_status_counts": dict(sorted(Counter(row.run_status for row in records).items())),
        "unscored_runs": sum(row.score_valid is not True for row in records),
    }
