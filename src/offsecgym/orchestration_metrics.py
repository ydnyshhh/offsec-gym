"""Trace-derived M6 orchestration diagnostics with explicit denominators."""

from __future__ import annotations

from collections.abc import Sequence

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionReservationAcquired,
    AnyTraceEvent,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    RunStarted,
    WorkerDebriefed,
    WorkerPacketPrepared,
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


def orchestration_metrics(trace: Sequence[AnyTraceEvent]) -> OrchestrationMetrics:
    dispatches = [item for item in trace if isinstance(item, ActionReservationAcquired)]
    previous: dict[str, set[object]] = {}
    exact = cross = within = 0
    for item in dispatches:
        owners = previous.setdefault(item.fingerprint, set())
        if owners:
            exact += 1
            if item.worker_id is not None:
                if any(owner is not None and owner != item.worker_id for owner in owners):
                    cross += 1
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
    time_to_first = (
        (first_valid_submission.occurred_at - started.occurred_at).total_seconds()
        if started is not None and first_valid_submission is not None
        else None
    )
    return OrchestrationMetrics(
        http_dispatches=len(dispatches),
        exact_repeat_dispatches=exact,
        cross_worker_repeat_dispatches=cross,
        within_worker_repeat_dispatches=within,
        cross_worker_duplication_rate=(
            cross / len(dispatches)
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
    )
