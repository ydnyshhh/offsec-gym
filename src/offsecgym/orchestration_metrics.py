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
    AdmissionDecision,
    AnyTraceEvent,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    PrerequisiteBootstrapCompleted,
    RunStarted,
    TaskBudgetExtended,
    TaskBudgetGranted,
    WorkerDebriefed,
    WorkerFinished,
    WorkerObjectiveAction,
    WorkerPacketPrepared,
    WorkerScheduled,
    WorkerStarted,
)
from offsecgym.storage.projection import ControllerProjection, project_controller_events


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
    admissible_unused_tokens: int = 0
    opportunity_displacement_tokens: int = 0
    ready_objectives: int | None = None
    admitted_objectives: int | None = None
    executed_objectives: int | None = None
    ready_coverage: float | None = None
    admission_coverage: float | None = None
    execution_coverage: float | None = None


NONIDENTITY_ROUTES = frozenset(
    {
        "GET /api/documents/{id}",
        "GET /api/invoices/{id}",
        "GET /api/support/tickets/{id}",
        "GET /api/public/invoices/{id}/preview",
        "POST /api/invoices/{id}/refund",
    }
)
NONIDENTITY_KINDS = frozenset({"document", "invoice", "ticket", "public", "refund"})


def _objective_stage_metrics(trace: Sequence[AnyTraceEvent]) -> dict[str, int | float | None]:
    decisions = [item for item in trace if isinstance(item, AdmissionDecision)]
    grants = [item for item in trace if isinstance(item, TaskBudgetGranted)]
    packets = [item for item in trace if isinstance(item, WorkerPacketPrepared)]
    if not decisions and not grants and not packets:
        return {}
    if decisions:
        ready = {
            kind
            for decision in decisions
            for kind, status in decision.task_states.items()
            if kind in NONIDENTITY_KINDS and status == "READY"
        }
        admitted = {item.worker_id for item in grants if item.kind in NONIDENTITY_KINDS}
        admitted_count = len({item.kind for item in grants if item.kind in NONIDENTITY_KINDS})
    else:
        ready = None
        admitted = {
            item.worker_id
            for item in packets
            if item.packet.contract is not None
            and item.packet.contract.route_family in NONIDENTITY_ROUTES
        }
        admitted_count = len(admitted)
    completed = {item.action_id for item in trace if isinstance(item, ActionCompleted)}
    executed = {
        item.worker_id
        for item in trace
        if isinstance(item, WorkerObjectiveAction)
        and item.worker_id in admitted
        and item.route_family in NONIDENTITY_ROUTES
        and item.action_id in completed
    }
    ready_count = len(ready) if ready is not None else None
    executed_count = len(executed)
    return {
        "ready_objectives": ready_count,
        "admitted_objectives": admitted_count,
        "executed_objectives": executed_count,
        "ready_coverage": ready_count / len(NONIDENTITY_KINDS) if ready_count is not None else None,
        "admission_coverage": (admitted_count / ready_count if ready_count else None),
        "execution_coverage": executed_count / admitted_count if admitted_count else None,
    }


def _free_admission_tokens(state: ControllerProjection) -> int:
    if state.budget is None or state.budget.max_total_tokens is None:
        return 0
    outstanding = sum(
        account.token_limit - account.used_tokens - account.reserved_tokens
        for account in state.worker_escrows.values()
    ) + sum(
        hold.request.minimum_viable_tokens for hold in state.admission_holds.values() if hold.active
    )
    return max(
        0,
        state.budget.max_total_tokens - state.used_tokens - state.reserved_tokens - outstanding,
    )


def _admission_opportunity_metrics(trace: Sequence[AnyTraceEvent]) -> tuple[int, int]:
    decisions = [item for item in trace if isinstance(item, AdmissionDecision)]
    if not decisions:
        return 0, 0
    state = project_controller_events(trace)
    latest = decisions[-1]
    unused = _free_admission_tokens(state)
    admissible_unused = (
        unused if any(minimum <= unused for minimum in latest.ready_minimum_tokens.values()) else 0
    )
    displaced = 0
    current_decision = None
    for index, item in enumerate(trace):
        if isinstance(item, AdmissionDecision):
            current_decision = item
        elif isinstance(item, TaskBudgetExtended) and current_decision is not None:
            unadmitted = {
                kind: minimum
                for kind, minimum in current_decision.ready_minimum_tokens.items()
                if kind not in current_decision.admitted_kinds
            }
            if not unadmitted:
                continue
            before = _free_admission_tokens(project_controller_events(trace[:index]))
            after = _free_admission_tokens(project_controller_events(trace[: index + 1]))
            if any(after < minimum <= before for minimum in unadmitted.values()):
                displaced += item.token_limit - item.prior_token_limit
    return admissible_unused, displaced


def orchestration_metrics(trace: Sequence[AnyTraceEvent]) -> OrchestrationMetrics:
    admissible_unused, opportunity_displacement = _admission_opportunity_metrics(trace)
    objective_stages = _objective_stage_metrics(trace)
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
        admissible_unused_tokens=admissible_unused,
        opportunity_displacement_tokens=opportunity_displacement,
        **objective_stages,
    )
