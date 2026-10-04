"""Read-only audit of the paired reporting boundary before outcome analysis."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from offsecgym.evaluation import RunEvaluation, evaluate_run, unscored_run
from offsecgym.providers.artifacts import ModelCallArtifacts, payload_sha256
from offsecgym.providers.base import ModelProvider
from offsecgym.providers.openai import request_wire_bytes
from offsecgym.research.m65_reporter import REPORTER_PROMPT, initial_evidence_index, reporter_tools
from offsecgym.research.m65_witness_packet import build_reporter_bundle
from offsecgym.research.m652_checkpoint import ProbeCheckpointStore
from offsecgym.schemas.events import (
    ActionBlocked,
    ActionCompleted,
    ActionFailed,
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    ReporterFinished,
    ReporterStarted,
    ReportingBranchStarted,
    RunCompleted,
)
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.schemas.specs import ModelSpec
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.reporting_branch import ReportingBranchStore, prefix_sha256


async def replay_branch_score(
    branch: ReportingBranchStore, oracle: GroundTruthManifest
) -> RunEvaluation:
    """Recompute a branch score from its isolated event stream and frozen oracle."""
    trace = await branch.read_run(branch.run_id)
    endings = [item for item in trace if isinstance(item, RunCompleted)]
    if len(endings) != 1:
        raise ValueError("reporting branch has no unique terminal status")
    status = endings[0].status
    findings = tuple(item.finding for item in trace if isinstance(item, FindingSubmitted))
    validations = tuple(item.result for item in trace if isinstance(item, FindingValidated))
    if status in {"completed", "budget_exhausted", "agent_failed"}:
        return evaluate_run(findings, validations, oracle, status=status)
    return unscored_run(
        status,
        len(findings),
        validated_count=sum(item.status == "validated" for item in validations),
        inconclusive=sum(item.status == "inconclusive" for item in validations),
    )


async def audit_paired_branches(
    source: PostgresEventStore,
    state_root: Path,
    run_id: UUID,
    branch_ids: dict[str, UUID],
    *,
    provider: ModelProvider,
    model: ModelSpec,
    expected_endpoint: tuple[str, str] | None = None,
) -> dict[str, object]:
    if set(branch_ids) != {"fresh", "continuation"} or len(set(branch_ids.values())) != 2:
        raise ValueError("audit requires one distinct branch for each arm")
    checkpoint = await ProbeCheckpointStore(source, state_root).load_latest(run_id)
    source_trace = list(await source.read_run(run_id))
    prefix = source_trace[: checkpoint.source_sequence + 1]
    source_sha = prefix_sha256(prefix)
    bundle = build_reporter_bundle(prefix, state_root, expected_run_id=run_id)
    expected_index = {"role": "user", "content": initial_evidence_index(bundle)}
    expected_continuation = [*checkpoint.base_items, *checkpoint.carry_items]
    if checkpoint.working_state_text:
        expected_continuation.append({"role": "user", "content": checkpoint.working_state_text})
    common_request: dict[str, object] | None = None
    arm_rows: dict[str, object] = {}
    for arm in ("fresh", "continuation"):
        branch_id = branch_ids[arm]
        events = await ReportingBranchStore(source, branch_id, run_id).read_run(run_id)
        if prefix_sha256(events[: len(prefix)]) != source_sha:
            raise ValueError("reporting arm source prefix differs")
        branch_events = events[len(prefix) :]
        if any(
            isinstance(item, (ActionRequested, ActionBlocked, ActionCompleted, ActionFailed))
            for item in branch_events
        ):
            raise ValueError("reporting arm issued a post-split gateway action")
        beginnings = [x for x in branch_events if isinstance(x, ReportingBranchStarted)]
        starts = [x for x in branch_events if isinstance(x, ReporterStarted)]
        finishes = [x for x in branch_events if isinstance(x, ReporterFinished)]
        endings = [x for x in branch_events if isinstance(x, RunCompleted)]
        if any(len(items) != 1 for items in (beginnings, starts, finishes, endings)):
            raise ValueError("reporting lifecycle has duplicate boundaries")
        beginning, started, finished, ending = (beginnings[0], starts[0], finishes[0], endings[0])
        expected_items = expected_continuation if arm == "continuation" else []
        if (
            branch_events[0] != beginning
            or beginning.branch_id != branch_id
            or beginning.arm != arm
            or beginning.checkpoint_id != checkpoint.checkpoint_id
            or beginning.source_trace_sha256 != source_sha
            or beginning.initial_context_sha256 != payload_sha256({"items": expected_items})
            or started.packet_sha256 != bundle.packet.bundle_sha256
            or started.source_trace_sha256 != source_sha
            or started.reporter_id != finished.reporter_id
            or started.task_id != finished.task_id
            or started.sequence_number >= finished.sequence_number
            or finished.sequence_number >= ending.sequence_number
        ):
            raise ValueError("reporting branch does not bind its checkpoint and packet")
        branch_findings = [x for x in branch_events if isinstance(x, FindingSubmitted)]
        validations = [x for x in branch_events if isinstance(x, FindingValidated)]
        source_findings = [x for x in prefix if isinstance(x, FindingSubmitted)]
        finding_ids = {x.finding.finding_id for x in (*source_findings, *branch_findings)}
        validation_ids = [x.result.finding_id for x in validations]
        if (
            tuple(x.finding.finding_id for x in branch_findings) != finished.submitted_finding_ids
            or any(
                not started.sequence_number < x.sequence_number < finished.sequence_number
                for x in branch_findings
            )
            or len(validation_ids) != len(set(validation_ids))
            or not set(validation_ids) <= finding_ids
            or (ending.status != "validation_failed" and set(validation_ids) != finding_ids)
            or any(x.sequence_number <= finished.sequence_number for x in validations)
        ):
            raise ValueError("reporting findings or validations are not branch attributable")
        calls = [x for x in branch_events if isinstance(x, ModelCallStarted)]
        completions = [x for x in branch_events if isinstance(x, ModelCallCompleted)]
        failures = [x for x in branch_events if isinstance(x, ModelCallFailed)]
        terminal_ids = [x.call_id for x in (*completions, *failures)]
        if (
            len({x.call_id for x in calls}) != len(calls)
            or len(terminal_ids) != len(set(terminal_ids))
            or {x.call_id for x in calls} != set(terminal_ids)
            or any(
                x.sequence_number >= finished.sequence_number
                for x in (*calls, *completions, *failures)
            )
        ):
            raise ValueError("reporting model calls lack one attributable terminal event")
        if expected_endpoint is not None and any(
            (x.resolved_model_revision, x.resolved_upstream_provider) != expected_endpoint
            for x in completions
        ):
            raise ValueError("reporting selected endpoint drifted")
        if len(calls) > (started.budget.max_model_calls or 0):
            raise ValueError("reporting arm exceeded its call budget")
        if sum(x.input_tokens + x.output_tokens for x in completions) > (
            started.budget.max_total_tokens or 0
        ):
            raise ValueError("reporting arm exceeded its token budget")
        if calls:
            first = calls[0]
            if first.request_artifact_id is None or first.request_sha256 is None:
                raise ValueError("reporting first request lacks a verified artifact")
            request = ModelCallArtifacts(state_root, branch_id=branch_id).read_verified(
                run_id,
                first.call_id,
                "request",
                first.request_artifact_id,
                first.request_sha256,
            )
            reconstructed = provider.prepare_request(
                model,
                REPORTER_PROMPT,
                [*expected_items, expected_index],
                reporter_tools(),
                min(
                    started.budget.max_total_tokens or 0,
                    started.budget.max_output_tokens_per_call or 0,
                ),
            )
            if request_wire_bytes(request) != request_wire_bytes(reconstructed):
                raise ValueError(
                    "reporting first request differs byte for byte from checkpoint reconstruction"
                )
            if payload_sha256(request) != first.request_sha256:
                raise ValueError("reporting first request event digest differs")
            common = {key: value for key, value in request.items() if key != "input"}
            if common_request is None:
                common_request = common
            elif common != common_request:
                raise ValueError("reporting arms differ beyond context carryover")
        elif finished.reason_code not in {
            "reporter_preflight",
            "reporter_token_budget",
            "reporter_wall_time_exhausted",
        }:
            raise ValueError("reporting arm lacks a first call without a budget reason")
        arm_rows[arm] = {
            "branch_id": str(branch_id),
            "reporter_status": finished.status,
            "run_status": ending.status,
            "reporter_reason": finished.reason_code,
            "model_calls": len(calls),
            "first_request_sha256": calls[0].request_sha256 if calls else None,
            "first_request_bytes_reconstructed": bool(calls),
            "input_tokens": sum(x.input_tokens for x in completions),
            "output_tokens": sum(x.output_tokens for x in completions),
            "new_finding_ids": tuple(str(x) for x in finished.submitted_finding_ids),
        }
    return {
        "source_run_id": str(run_id),
        "checkpoint_id": str(checkpoint.checkpoint_id),
        "source_trace_sha256": source_sha,
        "packet_sha256": bundle.packet.bundle_sha256,
        "arms": arm_rows,
    }
