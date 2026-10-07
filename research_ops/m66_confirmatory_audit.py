"""Read-only authoritative cell checks for M6.6 confirmatory collection."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from offsecgym.evaluation import evaluate_run, unscored_run
from offsecgym.experiment.scripted import experiment_hash
from offsecgym.research.m64_execute import _sha256
from offsecgym.schemas.events import (
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.storage.projection import project_controller_events
from offsecgym.storage.tables import runs

PRICE_IN = Decimal("0.000003")
PRICE_OUT = Decimal("0.000015")


def _trace_sha(trace: list) -> str:
    raw = (
        json.dumps([event.model_dump(mode="json") for event in trace], sort_keys=True) + "\n"
    ).encode()
    return _sha256(raw)


def _identity_sha(trace: list) -> str:
    identities = [[event.sequence_number, str(event.event_id)] for event in trace]
    return hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest()


def _expected_run_hash(spec, arm: str) -> str:
    if arm == "control":
        return experiment_hash(spec)
    record = {"spec": spec.model_dump(mode="json"), "policy": "m66-generic-temporal-witness-v1"}
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _stage_extract(
    protocol_root: Path,
    manifest_path: Path,
    state_dir: Path,
    cell: dict,
    record: dict,
    output: Path,
) -> str:
    if output.exists():
        raise ValueError("stage output already exists; reconciliation is required")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(protocol_root / "src")
    command = [
        sys.executable,
        str(protocol_root / "research_ops/m66_confirmatory_stages.py"),
        "--manifest",
        str(manifest_path),
        "--cell-id",
        cell["cell_id"],
        "--trace",
        record["trace_path"],
        "--trace-sha256",
        record["trace_sha256"],
        "--state-dir",
        str(state_dir),
        "--output",
        str(output),
    ]
    completed = subprocess.run(
        command, cwd=protocol_root, env=environment, text=True, capture_output=True, check=False
    )
    if completed.returncode != 0 or not output.is_file():
        raise ValueError(f"pinned confirmatory stage extraction failed for {cell['cell_id']}")
    stage = json.loads(output.read_bytes())
    if (
        stage.get("protocol") != "m66-confirmatory-v1"
        or stage.get("cell_id") != cell["cell_id"]
        or stage.get("manifest_sha256") != _sha256(manifest_path.read_bytes())
        or stage.get("trace_sha256") != record["trace_sha256"]
    ):
        raise ValueError("stage extraction output does not bind this confirmatory cell")
    return _sha256(output.read_bytes())


async def audit_cell(
    *,
    events,
    state,
    manifest: dict,
    manifest_path: Path,
    protocol_root: Path,
    state_dir: Path,
    journal_dir: Path,
    cell: dict,
    record: dict,
    spec,
    expected_stage_sha256: str | None = None,
) -> dict:
    """Replay one completed cell before admitting any later cell."""
    run_id = UUID(record["run_id"])
    trace = await events.read_run(run_id)
    trace_path = Path(record["trace_path"])
    if (
        not trace
        or [event.sequence_number for event in trace] != list(range(1, len(trace) + 1))
        or any(event.run_id != run_id for event in trace)
        or len({event.event_id for event in trace}) != len(trace)
        or not trace_path.is_file()
        or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
        or _trace_sha(trace) != record["trace_sha256"]
    ):
        raise ValueError("pilot trace differs from authoritative event stream")
    starts = [event for event in trace if isinstance(event, RunStarted)]
    terminals = [event for event in trace if isinstance(event, RunCompleted)]
    ranges = [event for event in trace if isinstance(event, RangeStarted)]
    if len(starts) != 1 or len(terminals) != 1 or len(ranges) != 1:
        raise ValueError("confirmatory run lacks exactly one start, range, and terminal event")
    if (
        starts[0].experiment_hash != _expected_run_hash(spec, cell["arm"])
        or terminals[0].status != record["status"]
        or terminals[0].sequence_number != len(trace)
        or ranges[0].build_id is None
    ):
        raise ValueError("confirmatory terminal status or build identity differs")
    if any(
        isinstance(
            event,
            (
                ActionRequested,
                ModelCallStarted,
                ModelCallCompleted,
                FindingSubmitted,
                FindingValidated,
            ),
        )
        and event.sequence_number > terminals[0].sequence_number
        for event in trace
    ):
        raise ValueError("pilot performed model or gateway work after terminal state")
    build = state.verify_build_integrity(ranges[0].build_id)
    if (
        str(build.build_id) != record["build_id"]
        or str(build.pair_id) != record["pair_id"]
        or build.artifact_digests.get("fixture.json") != record["fixture_digest"]
        or build.spec != spec.range
        or cell["experiment_sha256"] != record["experiment_sha256"]
    ):
        raise ValueError("pilot build, pair, fixture, or spec binding differs")
    projection = project_controller_events(trace)
    if (
        projection.active_workers
        or projection.active_actions
        or projection.active_coverage
        or projection.model_reservations
        or any(hold.active for hold in projection.admission_holds.values())
    ):
        raise ValueError("pilot retains an active controller reservation or lease")
    bootstrap = [
        event
        for event in trace
        if isinstance(event, ActionRequested) and event.source_phase == "bootstrap"
    ]
    bootstrap_complete = [
        event for event in trace if isinstance(event, PrerequisiteBootstrapCompleted)
    ]
    limit = 32 if cell["range_family"] == "saas" else 40
    if (
        len(bootstrap_complete) != 1
        or len(bootstrap) != record["bootstrap_actions"]
        or bootstrap_complete[0].action_count != len(bootstrap)
        or bootstrap_complete[0].http_request_count != record["bootstrap_http_requests"]
        or len(bootstrap) > limit
        or bootstrap_complete[0].http_request_count > limit
    ):
        raise ValueError("pilot bootstrap boundary differs from frozen allowance")
    model_starts = [event for event in trace if isinstance(event, ModelCallStarted)]
    model_calls = [event for event in trace if isinstance(event, ModelCallCompleted)]
    model_failures = [event for event in trace if isinstance(event, ModelCallFailed)]
    if (
        len(model_starts) != len(model_calls) + len(model_failures)
        or len(model_starts) != record["model_call_starts"]
        or len(model_calls) != record["model_calls"]
        or len({event.call_id for event in model_starts}) != len(model_starts)
        or {event.call_id for event in model_starts}
        != {event.call_id for event in model_calls} | {event.call_id for event in model_failures}
        or {event.call_id for event in model_calls} & {event.call_id for event in model_failures}
        or len(model_starts) > cell["max_model_calls"]
        or any(
            event.provider != "openrouter" or event.model != manifest["model_request"]["name"]
            for event in model_starts
        )
        or any(
            (event.resolved_model_revision, event.resolved_upstream_provider)
            != (
                manifest["expected_selected_endpoint"]["revision"],
                manifest["expected_selected_endpoint"]["upstream_provider"],
            )
            for event in model_calls
        )
    ):
        raise ValueError("confirmatory has an extra, missing, or drifted model call")
    if record["status"] == "provider_failed":
        if (
            len(model_failures) != 1
            or model_failures[0].reason_code != record.get("failure_reason")
            or record["score_valid"] is not False
        ):
            raise ValueError("provider-failed cell lacks one matching model failure")
    elif model_failures or record["status"] not in {
        "completed",
        "budget_exhausted",
        "agent_failed",
        "environment_failed",
        "validation_failed",
        "cancelled",
    }:
        raise ValueError("unsupported confirmatory terminal failure accounting")
    input_tokens = sum(event.input_tokens for event in model_calls)
    output_tokens = sum(event.output_tokens for event in model_calls)
    cost = Decimal(input_tokens) * PRICE_IN + Decimal(output_tokens) * PRICE_OUT
    if (
        input_tokens != record["input_tokens"]
        or output_tokens != record["output_tokens"]
        or input_tokens + output_tokens > cell["max_total_tokens"]
        or any(event.estimated_cost_usd is None for event in model_calls)
        or abs(sum(Decimal(str(event.estimated_cost_usd)) for event in model_calls) - cost)
        > Decimal("0.000001")
        or Decimal(str(record["estimated_cost_usd"])) != cost
    ):
        raise ValueError("pilot event, journal, and configured model costs differ")
    submissions = [event.finding for event in trace if isinstance(event, FindingSubmitted)]
    verdicts = [event.result for event in trace if isinstance(event, FindingValidated)]
    if (
        len({finding.finding_id for finding in submissions}) != len(submissions)
        or len({result.finding_id for result in verdicts}) != len(verdicts)
        or {finding.finding_id for finding in submissions}
        != {result.finding_id for result in verdicts}
        or any(finding.run_id != run_id for finding in submissions)
        or any(result.run_id is not None and result.run_id != run_id for result in verdicts)
    ):
        raise ValueError("pilot finding and validation linkage differs")
    oracle = GroundTruthManifest.model_validate_json(
        (state_dir / "oracles" / ranges[0].build_id.hex / "ground_truth.json").read_bytes()
    )
    verdict_by_id = {result.finding_id: result for result in verdicts}
    if record["status"] in {"completed", "budget_exhausted", "agent_failed"}:
        if set(verdict_by_id) != {finding.finding_id for finding in submissions}:
            raise ValueError("scored finding verdicts are incomplete")
        replay = evaluate_run(
            tuple(submissions),
            tuple(verdict_by_id[finding.finding_id] for finding in submissions),
            oracle,
            status=record["status"],
        )
    else:
        replay = unscored_run(
            record["status"],
            candidate_count=len(submissions),
            validated_count=sum(result.status == "validated" for result in verdicts),
            inconclusive=sum(result.status == "inconclusive" for result in verdicts),
        )
    if (
        replay.model_dump(mode="json") != record["evaluation"]
        or replay.score_valid != record["score_valid"]
    ):
        raise ValueError("confirmatory oracle score replay differs from journaled score")
    stage_path = journal_dir / "stages" / f"{cell['cell_id']}.json"
    if expected_stage_sha256 is None:
        stage_sha256 = _stage_extract(
            protocol_root, manifest_path, state_dir, cell, record, stage_path
        )
    else:
        if not stage_path.is_file() or _sha256(stage_path.read_bytes()) != expected_stage_sha256:
            raise ValueError("existing stage extraction differs from paired audit receipt")
        stage = json.loads(stage_path.read_bytes())
        if (
            stage.get("protocol") != "m66-confirmatory-v1"
            or stage.get("cell_id") != cell["cell_id"]
            or stage.get("manifest_sha256") != _sha256(manifest_path.read_bytes())
            or stage.get("trace_sha256") != record["trace_sha256"]
        ):
            raise ValueError("existing stage output does not bind this confirmatory cell")
        stage_sha256 = expected_stage_sha256
    stage = json.loads(stage_path.read_bytes())
    observed = stage.get("stages")
    if not isinstance(observed, dict) or any(
        observed.get(key) != expected
        for key, expected in (
            ("run_id", record["run_id"]),
            ("status", record["status"]),
            ("score_valid", record["score_valid"]),
            ("score_replay", record["evaluation"]),
            ("input_tokens", record["input_tokens"]),
            ("output_tokens", record["output_tokens"]),
            ("gateway_actions", record["gateway_actions"]),
        )
    ):
        raise ValueError("confirmatory stage output differs from authoritative cell replay")
    replay_run_ids = sorted(
        {
            str(result.replay_trace.replay_run_id)
            for result in verdicts
            if result.replay_trace is not None
        }
    )
    return {
        "cell_id": cell["cell_id"],
        "run_id": str(run_id),
        "event_count": len(trace),
        "event_identity_sha256": _identity_sha(trace),
        "trace_sha256": record["trace_sha256"],
        "stage_path": str(stage_path),
        "stage_sha256": stage_sha256,
        "build_id": record["build_id"],
        "pair_id": record["pair_id"],
        "fixture_digest": record["fixture_digest"],
        "estimated_cost_usd": str(cost),
        "validator_replay_run_ids": replay_run_ids,
    }


async def audit_pair_inventory(
    engine, events, completed: dict[str, dict], audits: list[dict]
) -> None:
    """Reject unjournaled source runs or uncited model work in the dedicated store."""
    source_ids = {UUID(record["run_id"]) for record in completed.values()}
    replay_ids = {UUID(value) for audit in audits for value in audit["validator_replay_run_ids"]}
    if source_ids & replay_ids:
        raise ValueError("pilot source and validator replay run identities overlap")
    async with engine.connect() as connection:
        authoritative_ids = set((await connection.execute(select(runs.c.run_id))).scalars().all())
    if authoritative_ids != source_ids | replay_ids:
        raise ValueError("dedicated event store contains an unjournaled pilot or replay run")
    for replay_id in replay_ids:
        trace = await events.read_run(replay_id)
        if (
            not trace
            or [event.sequence_number for event in trace] != list(range(1, len(trace) + 1))
            or any(event.run_id != replay_id for event in trace)
            or sum(isinstance(event, RunStarted) for event in trace) != 1
            or [event.status for event in trace if isinstance(event, RunCompleted)] != ["completed"]
            or any(isinstance(event, (ModelCallStarted, ModelCallCompleted)) for event in trace)
        ):
            raise ValueError("validator replay contains model work or inconsistent events")
