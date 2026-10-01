"""Trace-derived M6 orchestration diagnostics with explicit denominators."""

from __future__ import annotations

from collections.abc import Sequence

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import (
    ActionAttemptReserved,
    ActionBlocked,
    ActionCompleted,
    ActionRequested,
    ActionReservationAcquired,
    AnyTraceEvent,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    PrerequisiteBootstrapCompleted,
    RunStarted,
    WorkerDebriefed,
    WorkerFinished,
    WorkerPacketPrepared,
    WorkerScheduled,
    WorkerStarted,
)


class OrchestrationMetrics(StrictModel):
    http_dispatches: int
    exact_repeat_dispatches: int
    cross_worker_repeat_dispatches: int
    within_worker_repeat_dispatches: int
    cross_worker_duplication_rate: float | None
    worker_findings: int
    findings_reusing_cross_worker_evidence: int
    evidence_reuse_rate: float | None
    worker_packets: int
    max_worker_packet_chars: int
    worker_debriefs: int
    coordinator_model_calls: int
    time_to_first_valid_finding_seconds: float | None
    time_to_last_valid_finding_seconds: float | None = None
    reservation_conflicts: int = 0
    reservation_conflict_rate: float | None = None
    worker_overlap_pairs: int = 0
    mean_worker_queue_wait_seconds: float | None = None
    bootstrap_http_dispatches: int = 0
    worker_http_dispatches: int = 0
    origin_linked_worker_dispatches: int = 0
    exact_repeat_source_turns: int = 0
    tokens_on_turns_with_exact_repeats: int = 0
    bootstrap_snapshot_hash: str | None = None
    worker_exact_repeat_dispatches: int = 0
    worker_repeats_of_bootstrap: int = 0


def orchestration_metrics(trace: Sequence[AnyTraceEvent]) -> OrchestrationMetrics:
    dispatches = [item for item in trace if isinstance(item, ActionReservationAcquired)]
    previous: dict[str, set[object]] = {}
    exact = cross = within = worker_exact = bootstrap_repeats = 0
    for item in dispatches:
        owners = previous.setdefault(item.fingerprint, set())
        if owners:
            exact += 1
            if item.worker_id is not None:
                worker_exact += 1
                if any(owner is not None and owner != item.worker_id for owner in owners):
                    cross += 1
                elif owners == {None}:
                    bootstrap_repeats += 1
                else:
                    within += 1
        owners.add(item.worker_id)
    evidence_owners = {
        item.evidence_id: item.worker_id
        for item in trace
        if isinstance(item, ActionCompleted) and item.evidence_id is not None
    }
    findings = [
        item for item in trace if isinstance(item, FindingSubmitted) and item.worker_id is not None
    ]
    reused = sum(
        any(
            evidence_owners.get(ref.evidence_id) is not None
            and evidence_owners[ref.evidence_id] != item.worker_id
            for ref in item.finding.evidence
        )
        for item in findings
    )
    packets = [item for item in trace if isinstance(item, WorkerPacketPrepared)]
    validated_ids = {
        item.result.finding_id
        for item in trace
        if isinstance(item, FindingValidated) and item.result.status == "validated"
    }
    started = next((item for item in trace if isinstance(item, RunStarted)), None)
    first_valid_submission = next(
        (
            item
            for item in trace
            if isinstance(item, FindingSubmitted) and item.finding.finding_id in validated_ids
        ),
        None,
    )
    valid_submissions = [
        item
        for item in trace
        if isinstance(item, FindingSubmitted) and item.finding.finding_id in validated_ids
    ]
    time_to_first = (
        (first_valid_submission.occurred_at - started.occurred_at).total_seconds()
        if started is not None and first_valid_submission is not None
        else None
    )
    time_to_last = (
        (valid_submissions[-1].occurred_at - started.occurred_at).total_seconds()
        if started is not None and valid_submissions
        else None
    )
    attempts = sum(isinstance(item, ActionAttemptReserved) for item in trace)
    conflicts = sum(
        isinstance(item, ActionBlocked) and item.reason_code == "action_already_reserved"
        for item in trace
    )
    scheduled = {item.worker_id: item for item in trace if isinstance(item, WorkerScheduled)}
    worker_starts = {item.worker_id: item for item in trace if isinstance(item, WorkerStarted)}
    worker_finishes = {item.worker_id: item for item in trace if isinstance(item, WorkerFinished)}
    intervals = [
        (item.occurred_at, worker_finishes[worker_id].occurred_at)
        for worker_id, item in worker_starts.items()
        if worker_id in worker_finishes
    ]
    overlap_pairs = sum(
        first_start < second_end and second_start < first_end
        for index, (first_start, first_end) in enumerate(intervals)
        for second_start, second_end in intervals[index + 1 :]
    )
    waits = [
        (item.occurred_at - scheduled[worker_id].occurred_at).total_seconds()
        for worker_id, item in worker_starts.items()
        if worker_id in scheduled
    ]
    requests = {item.action_id: item for item in trace if isinstance(item, ActionRequested)}
    calls = {item.call_id: item for item in trace if isinstance(item, ModelCallCompleted)}
    previous_action: dict[str, object] = {}
    completed_actions: set[object] = set()
    repeated_turns: set[object] = set()
    for item in trace:
        if isinstance(item, ActionCompleted):
            completed_actions.add(item.action_id)
        elif isinstance(item, ActionReservationAcquired):
            request = requests.get(item.action_id)
            if (
                previous_action.get(item.fingerprint) in completed_actions
                and request is not None
                and request.originating_call_id in calls
            ):
                repeated_turns.add(request.originating_call_id)
            previous_action[item.fingerprint] = item.action_id
    bootstrap_done = next(
        (item for item in trace if isinstance(item, PrerequisiteBootstrapCompleted)), None
    )
    return OrchestrationMetrics(
        http_dispatches=len(dispatches),
        exact_repeat_dispatches=exact,
        cross_worker_repeat_dispatches=cross,
        within_worker_repeat_dispatches=within,
        cross_worker_duplication_rate=(
            cross / sum(item.worker_id is not None for item in dispatches)
            if any(item.worker_id is not None for item in dispatches)
            else None
        ),
        worker_findings=len(findings),
        findings_reusing_cross_worker_evidence=reused,
        evidence_reuse_rate=reused / len(findings) if findings else None,
        worker_packets=len(packets),
        max_worker_packet_chars=max(
            (len(item.packet.model_dump_json()) for item in packets), default=0
        ),
        worker_debriefs=sum(isinstance(item, WorkerDebriefed) for item in trace),
        coordinator_model_calls=sum(
            isinstance(item, ModelCallCompleted) and item.worker_id is None for item in trace
        )
        if packets
        else 0,
        time_to_first_valid_finding_seconds=time_to_first,
        time_to_last_valid_finding_seconds=time_to_last,
        reservation_conflicts=conflicts,
        reservation_conflict_rate=conflicts / attempts if attempts else None,
        worker_overlap_pairs=overlap_pairs,
        mean_worker_queue_wait_seconds=sum(waits) / len(waits) if waits else None,
        bootstrap_http_dispatches=sum(
            requests.get(item.action_id) is not None
            and requests[item.action_id].source_phase == "bootstrap"
            for item in dispatches
        ),
        worker_http_dispatches=sum(item.worker_id is not None for item in dispatches),
        origin_linked_worker_dispatches=sum(
            item.worker_id is not None
            and requests.get(item.action_id) is not None
            and requests[item.action_id].originating_call_id in calls
            and requests[item.action_id].originating_tool_call_id is not None
            for item in dispatches
        ),
        exact_repeat_source_turns=len(repeated_turns),
        tokens_on_turns_with_exact_repeats=sum(
            calls[call_id].input_tokens + calls[call_id].output_tokens for call_id in repeated_turns
        ),
        bootstrap_snapshot_hash=bootstrap_done.snapshot_hash if bootstrap_done else None,
        worker_exact_repeat_dispatches=worker_exact,
        worker_repeats_of_bootstrap=bootstrap_repeats,
    )
