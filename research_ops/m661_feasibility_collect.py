"""Guarded, one-pass collection of the approved M6.6.1 excluded feasibility cells.

Run with PYTHONPATH at the exact source checkout. The manifest and analysis live
at the exact protocol commit; this operational wrapper cannot change either.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import stat
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

import offsecgym
from offsecgym.evaluation import evaluate_run, unscored_run
from offsecgym.experiment.critical_checkpoint import CriticalCheckpointExperimentRunner
from offsecgym.experiment.scripted import experiment_hash
from offsecgym.providers.base import ModelTurn, ProviderFailure, ProviderRequestError
from offsecgym.providers.openrouter import OpenRouterResponsesProvider, selected_endpoint
from offsecgym.research.m64_execute import Journal, _sha256, _utc_now
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.events import (
    CriticalCheckpointBound,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events
from offsecgym.storage.tables import runs

EXPECTED_MANIFEST_SHA256 = "88cda1e9d8977644cc7d7447c44dda15c562bb9e6899feacf935537344271373"
EXPECTED_ASSIGNMENT_SHA256 = "05373c9c3f9dd9099403bee7e998f92193072618422675aa9477f7a84a90d8f2"
EXPECTED_APPROVAL_SHA256 = "7ff3db6492353baf794000b26c7151608ec6fcd005bf2b825043874fbf451ed8"
SOURCE_COMMIT = "e302b182a6987c84ac542f50c36932de249c99c7"
PROTOCOL_COMMIT = "d0cd222be911c72fd26eef777bd77bd6234ed383"
MODEL = "moonshotai/kimi-k3"
REVISION = "moonshotai/kimi-k3-20260715"
UPSTREAM = "Moonshot AI"
ENDPOINT_URL = "https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints"
PRICE_IN = Decimal("0.000003")
PRICE_OUT = Decimal("0.000015")
CELL_RESERVATION = Decimal("1.80")
CAP = Decimal("172.80")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _clean(root: Path) -> None:
    if subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root, text=True
    ):
        raise ValueError("pinned checkout is not clean")


def _commit_bytes(root: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=root)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("pinned protocol module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _approval_and_manifest(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
) -> tuple[dict, dict]:
    if any(value == "0" * 64 for value in (EXPECTED_MANIFEST_SHA256, EXPECTED_APPROVAL_SHA256)):
        raise ValueError("paid feasibility collection is closed")
    if _head(source_root) != SOURCE_COMMIT or _head(protocol_root) != PROTOCOL_COMMIT:
        raise ValueError("source or protocol checkout differs from frozen commit")
    for root in (execution_root, source_root, protocol_root):
        _clean(root)
    if not Path(offsecgym.__file__).resolve().is_relative_to((source_root / "src").resolve()):
        raise ValueError("runtime imports are outside pinned source checkout")
    if manifest_path != execution_root / "experiments/manifests/m661-feasibility-v1.json":
        raise ValueError("manifest path differs from merged execution checkout")
    raw = manifest_path.read_bytes()
    if _sha256(raw) != EXPECTED_MANIFEST_SHA256:
        raise ValueError("frozen manifest bytes differ")
    manifest = json.loads(raw)
    if approval_path != execution_root.parent / "m661-feasibility-v1/approval.json":
        # Operational evidence is private and must stay outside Git worktrees.
        raise ValueError("private approval path differs")
    if (
        approval_path.is_symlink()
        or not approval_path.is_file()
        or stat.S_IMODE(approval_path.stat().st_mode) & 0o077
    ):
        raise ValueError("private approval receipt is absent or not private")
    approval_bytes = approval_path.read_bytes()
    if _sha256(approval_bytes) != EXPECTED_APPROVAL_SHA256:
        raise ValueError("private approval bytes differ")
    approval = json.loads(approval_bytes)
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    frozen_at = datetime.fromisoformat(
        manifest["price_snapshot"]["checked_at"].replace("Z", "+00:00")
    )
    if approved_at.utcoffset() is None or approved_at < frozen_at:
        raise ValueError("approval timestamp precedes frozen manifest")
    if (
        manifest.get("protocol") != "m661-feasibility-v1"
        or manifest.get("source_commit") != SOURCE_COMMIT
        or manifest.get("protocol_commit") != PROTOCOL_COMMIT
        or manifest.get("planned_cells") != 96
        or manifest.get("paid_model_calls_authorized") is not False
        or manifest.get("no_retry_or_replacement") is not True
        or manifest.get("schedule", {}).get("cell_assignment_sha256") != EXPECTED_ASSIGNMENT_SHA256
        or Decimal(str(manifest.get("cumulative_estimated_cost_stop_usd"))) != CAP
        or approval.get("protocol") != manifest["protocol"]
        or approval.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("assignment_sha256") != EXPECTED_ASSIGNMENT_SHA256
        or approval.get("source_commit") != SOURCE_COMMIT
        or approval.get("protocol_commit") != PROTOCOL_COMMIT
        or approval.get("approval_scope") != "excluded_96_assigned_cells_once_only"
        or approval.get("approved_by") != "human_user"
        or approval.get("approved_for_paid_calls") is not True
        or approval.get("no_retry_or_replacement") is not True
        or Decimal(str(approval.get("cost_ceiling_usd"))) != CAP
    ):
        raise ValueError("approval and manifest do not bind the same exact study")
    if _sha256(_canonical(manifest["cells"])) != EXPECTED_ASSIGNMENT_SHA256:
        raise ValueError("frozen assignment order or bytes differ")
    for root, commit, key in (
        (source_root, SOURCE_COMMIT, "source_file_hashes"),
        (protocol_root, PROTOCOL_COMMIT, "protocol_file_hashes"),
    ):
        for path, digest in manifest[key].items():
            data = (root / path).read_bytes()
            if _sha256(data) != digest or data != _commit_bytes(root, commit, path):
                raise ValueError(f"pinned source or protocol file differs: {path}")
    config_path = manifest["config"]["path"]
    if (
        manifest["source_file_hashes"][config_path] != manifest["protocol_file_hashes"][config_path]
        or manifest["config"]["sha256"] != manifest["source_file_hashes"][config_path]
    ):
        raise ValueError("execution config differs across source and protocol commits")
    planner = _load_module(
        protocol_root / "src/offsecgym/research/m661_feasibility_protocol.py",
        "_m661_pinned_feasibility_protocol",
    )
    rebuilt = planner.plan_feasibility(
        protocol_root,
        protocol_commit=PROTOCOL_COMMIT,
        endpoint_receipt=manifest["price_snapshot"],
    )
    if json.loads(_canonical(rebuilt)) != manifest:
        raise ValueError("frozen manifest does not rebuild from pinned protocol")
    cells = manifest["cells"]
    if (
        len(cells) != 96
        or [cell["order"] for cell in cells] != list(range(1, 97))
        or len({cell["cell_id"] for cell in cells}) != 96
        or any(cell["max_total_tokens"] != 120_000 for cell in cells)
        or CELL_RESERVATION * len(cells) != CAP
    ):
        raise ValueError("frozen feasibility assignment structure differs")
    return manifest, approval


def _spec(source_root: Path, manifest: dict, cell: dict) -> ExperimentSpec:
    path = source_root / manifest["config"]["path"]
    base = ExperimentSpec.model_validate(yaml.safe_load(path.read_bytes()))
    spec = ExperimentSpec.model_validate(
        base.model_copy(
            update={
                "seed": cell["seed"],
                "range": base.range.model_copy(
                    update={"seed": cell["seed"], "patched": cell["variant"] == "patched"}
                ),
            }
        ).model_dump(mode="python")
    )
    if (
        experiment_hash(spec) != cell["experiment_sha256"]
        or spec.range.seed != cell["seed"]
        or spec.budget.max_total_tokens != cell["max_total_tokens"]
        or spec.budget.max_model_calls != cell["max_model_calls"]
        or spec.budget.max_actions != cell["max_actions"]
        or spec.budget.max_http_requests != cell["max_http_requests"]
        or spec.model is None
        or spec.model.name != MODEL
        or spec.model.upstream_provider != "moonshotai"
        or spec.model.reasoning != "high"
    ):
        raise ValueError("cell spec differs from frozen source config and assignment")
    return spec


def _endpoint(manifest: dict) -> dict:
    from urllib.request import urlopen

    with urlopen(ENDPOINT_URL, timeout=20) as response:
        raw = response.read(2_000_000)
    selected = [
        item
        for item in json.loads(raw)["data"]["endpoints"]
        if item.get("name") == f"{UPSTREAM} | {REVISION}"
    ]
    if (
        len(selected) != 1
        or selected[0].get("provider_name") != UPSTREAM
        or selected[0].get("model_id") != MODEL
        or selected[0].get("status") != 0
        or Decimal(selected[0]["pricing"]["prompt"]) != PRICE_IN
        or Decimal(selected[0]["pricing"]["completion"]) != PRICE_OUT
        or not {"tools", "tool_choice", "reasoning_effort"}
        <= set(selected[0].get("supported_parameters", []))
        or manifest["expected_selected_endpoint"]
        != {"revision": REVISION, "upstream_provider": UPSTREAM}
    ):
        raise ValueError("selected public endpoint or price drifted")
    return {
        "checked_at": _utc_now(),
        "response_sha256": _sha256(raw),
        "endpoint": selected[0]["name"],
        "model_id": MODEL,
        "revision": REVISION,
        "upstream_provider": UPSTREAM,
        "status": selected[0]["status"],
        "input_usd_per_million_tokens": 3.0,
        "output_usd_per_million_tokens": 15.0,
        "source": ENDPOINT_URL,
    }


class EndpointGuard:
    def __init__(self) -> None:
        self.stopped = False

    def provider(self, api_key: str) -> OpenRouterResponsesProvider:
        guard = self

        class Guarded(OpenRouterResponsesProvider):
            async def complete(self, request_payload: dict[str, object]) -> ModelTurn:
                if guard.stopped:
                    raise ProviderFailure("feasibility_provider_stopped")
                if (
                    request_payload.get("model") != MODEL
                    or request_payload.get("provider")
                    != {"order": ["moonshotai"], "allow_fallbacks": False}
                    or request_payload.get("reasoning") != {"effort": "high"}
                    or request_payload.get("store") is not False
                ):
                    guard.stopped = True
                    raise ProviderRequestError("feasibility_request_policy_drift")
                try:
                    turn = await super().complete(request_payload)
                    if selected_endpoint(turn.raw_response) != (REVISION, UPSTREAM):
                        guard.stopped = True
                        raise ProviderFailure(
                            "feasibility_endpoint_drift", raw_response=turn.raw_response
                        )
                    return turn
                except ProviderRequestError:
                    guard.stopped = True
                    raise
                except ProviderFailure:
                    raise  # Isolated transient failures remain assigned after replay.
                except Exception:
                    guard.stopped = True
                    raise

        return Guarded(api_key)


def _write_json(path: Path, value: object) -> str:
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    return _write_bytes(path, raw)


def _write_bytes(path: Path, raw: bytes) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha256(raw)


def _trace_sha(trace: list) -> str:
    return _sha256(
        (
            json.dumps([event.model_dump(mode="json") for event in trace], sort_keys=True) + "\n"
        ).encode()
    )


def _identity_sha(trace: list) -> str:
    return _sha256(_canonical([[event.sequence_number, str(event.event_id)] for event in trace]))


def _health_decision(records: list[dict]) -> str:
    if not records or records[-1]["status"] != "provider_failed":
        return "continue"
    transient = {
        "provider_unavailable",
        "provider_timeout",
        "provider_server_error",
        "provider_rate_limited",
    }
    transport = {"provider_unavailable", "provider_timeout", "provider_server_error"}
    if records[-1].get("failure_reason") not in transient:
        return "stop"
    recent = records[-10:]
    if sum(item["status"] == "provider_failed" for item in recent) >= 3:
        return "pause"
    if len(records) >= 2 and all(
        item["status"] == "provider_failed" and item.get("failure_reason") in transport
        for item in records[-2:]
    ):
        return "pause"
    return "continue"


async def _audit_cell(
    events, state, analysis, cell: dict, record: dict, stage_path: Path | None
) -> dict:
    run_id = UUID(record["run_id"])
    trace = await events.read_run(run_id)
    if (
        not trace
        or [item.sequence_number for item in trace] != list(range(1, len(trace) + 1))
        or any(item.run_id != run_id for item in trace)
        or len({item.event_id for item in trace}) != len(trace)
        or _trace_sha(trace) != record["trace_sha256"]
        or _identity_sha(trace) != record["event_identity_sha256"]
        or _sha256(Path(record["trace_path"]).read_bytes()) != record["trace_sha256"]
    ):
        raise ValueError("source trace differs from authoritative PostgreSQL")
    terminals = [item for item in trace if isinstance(item, RunCompleted)]
    starts = [item for item in trace if isinstance(item, RunStarted)]
    ranges = [item for item in trace if isinstance(item, RangeStarted)]
    bounds = [item for item in trace if isinstance(item, CriticalCheckpointBound)]
    if (
        len(terminals) != 1
        or len(starts) != 1
        or len(ranges) != 1
        or len(bounds) != 1
        or terminals[0].sequence_number != len(trace)
        or starts[0].experiment_hash != cell["experiment_sha256"]
        or terminals[0].status != record["status"]
        or str(bounds[0].setup_run_id) != record["setup_run_id"]
        or bounds[0].receipt_sha256 != record["receipt_sha256"]
    ):
        raise ValueError("source start, terminal, or checkpoint binding differs")
    build = state.verify_build_integrity(ranges[0].build_id)
    if (
        str(build.build_id) != record["build_id"]
        or build.artifact_digests.get("fixture.json") != record["fixture_digest"]
        or str(build.pair_id) != record["pair_id"]
        or build.spec.seed != cell["seed"]
        or build.spec.patched != (cell["variant"] == "patched")
    ):
        raise ValueError("source build, fixture, or variant differs")
    projection = project_controller_events(trace)
    if (
        projection.active_actions
        or projection.model_reservations
        or projection.active_workers
        or any(hold.active for hold in projection.admission_holds.values())
    ):
        raise ValueError("source retains an active controller reservation or lease")
    calls = [item for item in trace if isinstance(item, ModelCallCompleted)]
    starts_model = [item for item in trace if isinstance(item, ModelCallStarted)]
    failed_model = [item for item in trace if isinstance(item, ModelCallFailed)]
    start_ids = {item.call_id for item in starts_model}
    completed_ids = {item.call_id for item in calls}
    failed_ids = {item.call_id for item in failed_model}
    if (
        len(starts_model) > cell["max_model_calls"]
        or len(start_ids) != len(starts_model)
        or len(completed_ids) != len(calls)
        or len(failed_ids) != len(failed_model)
        or start_ids != completed_ids | failed_ids
        or completed_ids & failed_ids
        or record["resource_auditable"] != (not failed_model)
        or record["model_call_starts"] != len(starts_model)
        or record["model_calls"] != len(calls)
        or record["input_tokens"] != sum(item.input_tokens for item in calls)
        or record["output_tokens"] != sum(item.output_tokens for item in calls)
        or any(item.provider != "openrouter" or item.model != MODEL for item in starts_model)
        or any(
            (item.resolved_model_revision, item.resolved_upstream_provider) != (REVISION, UPSTREAM)
            for item in calls
        )
        or sum(item.input_tokens + item.output_tokens for item in calls) > cell["max_total_tokens"]
    ):
        raise ValueError("source model request or selected endpoint drifted")
    if record["status"] == "provider_failed":
        if (
            len(failed_model) != 1
            or failed_model[0].reason_code != record.get("failure_reason")
            or record["score_valid"] is not False
        ):
            raise ValueError("provider failure does not match source events")
    elif failed_model:
        raise ValueError("non-provider cell has a failed model call")
    cost = sum(
        (
            Decimal(item.input_tokens) * PRICE_IN + Decimal(item.output_tokens) * PRICE_OUT
            for item in calls
        ),
        Decimal(0),
    )
    if any(item.estimated_cost_usd is None for item in calls) or abs(
        sum((Decimal(str(item.estimated_cost_usd)) for item in calls), Decimal(0)) - cost
    ) > Decimal("0.000001"):
        raise ValueError("source model cost replay differs")
    if Decimal(str(record["estimated_cost_usd"])) != cost:
        raise ValueError("journaled token cost differs")
    receipt_path = state.root / "critical_checkpoints" / f"{run_id.hex}.json"
    if _sha256(receipt_path.read_bytes()) != record["receipt_sha256"]:
        raise ValueError("private checkpoint receipt differs")
    setup_trace = await events.read_run(UUID(record["setup_run_id"]))
    if (
        not setup_trace
        or [item.sequence_number for item in setup_trace] != list(range(1, len(setup_trace) + 1))
        or any(item.run_id != UUID(record["setup_run_id"]) for item in setup_trace)
        or [item.status for item in setup_trace if isinstance(item, RunCompleted)] != ["completed"]
        or any(isinstance(item, ModelCallStarted) for item in setup_trace)
    ):
        raise ValueError("trusted setup trace differs or contains model work")
    oracle = StateOracleStore(state)._load_ground_truth(build.build_id)
    fixture = json.loads((state.build_dir(build.build_id) / "fixture.json").read_bytes())
    row = await analysis.extract_checkpoint_assignment(
        {**cell, "model_run_id": record["run_id"]}, state.root, events, oracle, fixture
    )
    if not row["trace_auditable"] or row["terminal_status"] != record["status"]:
        raise ValueError("checkpoint analysis could not audit the completed source")
    if stage_path is not None:
        if _write_json(stage_path, row) != record["stage_sha256"]:
            raise ValueError("new checkpoint stage hash differs")
    else:
        saved_stage = Path(record["stage_path"]).read_bytes()
        rebuilt_stage = (json.dumps(row, sort_keys=True, indent=2) + "\n").encode()
        if saved_stage != rebuilt_stage or _sha256(saved_stage) != record["stage_sha256"]:
            raise ValueError("saved checkpoint stage differs")
    submissions = [item.finding for item in trace if isinstance(item, FindingSubmitted)]
    verdicts = [item.result for item in trace if isinstance(item, FindingValidated)]
    by_id = {item.finding_id: item for item in verdicts}
    if (
        len({item.finding_id for item in submissions}) != len(submissions)
        or len(by_id) != len(verdicts)
        or set(by_id) != {item.finding_id for item in submissions}
        or any(item.run_id != run_id for item in submissions)
        or any(item.run_id is not None and item.run_id != run_id for item in verdicts)
    ):
        raise ValueError("finding and validation event linkage differs")
    if record["score_valid"]:
        replay = evaluate_run(
            tuple(submissions),
            tuple(by_id[item.finding_id] for item in submissions),
            oracle,
            status=record["status"],
        )
    else:
        replay = unscored_run(
            record["status"],
            candidate_count=len(submissions),
            validated_count=sum(item.status == "validated" for item in verdicts),
            inconclusive=sum(item.status == "inconclusive" for item in verdicts),
        )
    if (
        replay.model_dump(mode="json") != record["evaluation"]
        or row["score_valid"] != record["score_valid"]
    ):
        raise ValueError("frozen score replay differs")
    replay_ids = sorted(
        {
            str(item.result.replay_trace.replay_run_id)
            for item in trace
            if isinstance(item, FindingValidated) and item.result.replay_trace is not None
        }
    )
    return {
        "cell_id": cell["cell_id"],
        "run_id": record["run_id"],
        "setup_run_id": record["setup_run_id"],
        "event_count": len(trace),
        "event_identity_sha256": record["event_identity_sha256"],
        "setup_event_identity_sha256": _identity_sha(setup_trace),
        "trace_sha256": record["trace_sha256"],
        "stage_sha256": record["stage_sha256"],
        "validator_replay_run_ids": replay_ids,
        "estimated_cost_usd": str(cost),
    }


async def _inventory(engine, events, records: list[dict], audits: list[dict]) -> None:
    source_ids = {UUID(item["run_id"]) for item in records}
    setup_ids = {UUID(item["setup_run_id"]) for item in records}
    replay_ids = {UUID(value) for audit in audits for value in audit["validator_replay_run_ids"]}
    if (
        len(source_ids) != len(records)
        or len(setup_ids) != len(records)
        or (source_ids & setup_ids or source_ids & replay_ids or setup_ids & replay_ids)
    ):
        raise ValueError("source, setup, or validator replay run identity overlaps")
    async with engine.connect() as connection:
        actual = set((await connection.execute(select(runs.c.run_id))).scalars().all())
    if actual != source_ids | setup_ids | replay_ids:
        raise ValueError("dedicated PostgreSQL contains an extra or missing run")
    for replay_id in replay_ids:
        trace = await events.read_run(replay_id)
        if (
            not trace
            or [item.sequence_number for item in trace] != list(range(1, len(trace) + 1))
            or any(isinstance(item, (ModelCallStarted, ModelCallCompleted)) for item in trace)
            or [item.status for item in trace if isinstance(item, RunCompleted)] != ["completed"]
        ):
            raise ValueError("validator replay contains model work or inconsistent events")


def _record(cell: dict, outcome, trace: list, build, state_root: Path, stage_path: Path) -> dict:
    run_id = outcome.run_id
    receipt_path = state_root / "critical_checkpoints" / f"{run_id.hex}.json"
    receipt = json.loads(receipt_path.read_bytes())
    calls = [item for item in trace if isinstance(item, ModelCallCompleted)]
    starts = [item for item in trace if isinstance(item, ModelCallStarted)]
    cost = sum(
        (
            Decimal(item.input_tokens) * PRICE_IN + Decimal(item.output_tokens) * PRICE_OUT
            for item in calls
        ),
        Decimal(0),
    )
    return {
        "type": "cell_completed",
        "cell_id": cell["cell_id"],
        "order": cell["order"],
        "at": _utc_now(),
        "run_id": str(run_id),
        "setup_run_id": receipt["setup_run_id"],
        "build_id": str(outcome.build_id),
        "pair_id": str(build.pair_id),
        "fixture_digest": build.artifact_digests["fixture.json"],
        "experiment_sha256": cell["experiment_sha256"],
        "status": outcome.evaluation.status,
        "score_valid": outcome.evaluation.score_valid,
        "evaluation": outcome.evaluation.model_dump(mode="json"),
        "failure_reason": outcome.failure_reason,
        "model_call_starts": len(starts),
        "model_calls": len(calls),
        "input_tokens": sum(item.input_tokens for item in calls),
        "output_tokens": sum(item.output_tokens for item in calls),
        "resource_auditable": len(starts) == len(calls),
        "estimated_cost_usd": str(cost),
        "receipt_sha256": _sha256(receipt_path.read_bytes()),
        "trace_sha256": _trace_sha(trace),
        "event_identity_sha256": _identity_sha(trace),
        "stage_path": str(stage_path),
        "stage_sha256": "",  # computed before journal append
    }


async def collect(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, approval = _approval_and_manifest(
        execution_root, source_root, protocol_root, manifest_path, approval_path
    )
    if (
        journal_path.name != "journal.jsonl"
        or state_dir.name != "range-state"
        or journal_path.is_symlink()
        or state_dir.is_symlink()
    ):
        raise ValueError("private journal or state path differs")
    if journal_path.exists() or state_dir.exists():
        raise ValueError("one-pass study evidence already exists; do not restart or retry")
    if journal_path.parent != approval_path.parent or state_dir.parent != approval_path.parent:
        raise ValueError("private study artifact directory differs")
    key = os.getenv("OPENROUTER_API_KEY")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not key or not database_url:
        raise ValueError("private model key and dedicated database URL are required")
    engine = create_async_engine(database_url)
    events = PostgresEventStore(engine)
    try:
        async with engine.connect() as connection:
            if (await connection.execute(select(runs.c.run_id))).first() is not None:
                raise ValueError("dedicated PostgreSQL is not empty before collection")
        # A final public check precedes journal creation and every paid pair.
        _endpoint(manifest)
        analysis = _load_module(
            protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
            "_m661_pinned_checkpoint_analysis",
        )
        runtime = ComposeRangeRuntime(state_dir)
        guard = EndpointGuard()
        journal = Journal(
            journal_path,
            manifest_sha256=EXPECTED_MANIFEST_SHA256,
            expected_endpoint=(REVISION, UPSTREAM),
            max_estimated_usd=float(approval["cost_ceiling_usd"]),
        )
        records: list[dict] = []
        audits: list[dict] = []
        committed_cost = Decimal(0)
        conservative_cost = Decimal(0)
        try:
            cells = manifest["cells"]
            for pair_index in range(48):
                pair = cells[2 * pair_index : 2 * pair_index + 2]
                if (
                    pair[0]["seed"] != pair[1]["seed"]
                    or pair[0]["checkpoint_id"] != pair[1]["checkpoint_id"]
                    or {pair[0]["variant"], pair[1]["variant"]} != {"vulnerable", "patched"}
                ):
                    raise ValueError("frozen pair structure differs")
                endpoint = _endpoint(manifest)
                endpoint_sha = _write_json(
                    journal_path.parent / f"endpoint-preflight-{pair_index + 1}.json", endpoint
                )
                pair_audits: list[dict] = []
                for cell in pair:
                    if conservative_cost + CELL_RESERVATION > CAP:
                        raise ValueError("next cell cannot fit approved cumulative reservation")
                    spec = _spec(source_root, manifest, cell)
                    run_id = uuid4()
                    journal.append(
                        {
                            "type": "cell_started",
                            "cell_id": cell["cell_id"],
                            "order": cell["order"],
                            "run_id": str(run_id),
                            "at": _utc_now(),
                        }
                    )
                    outcome = await CriticalCheckpointExperimentRunner(
                        runtime, events, guard.provider(key), cell["checkpoint_id"]
                    ).run(spec, run_id=run_id)
                    if outcome.build_id is None:
                        raise ValueError("completed cell lacks a bound build")
                    build = runtime.state.verify_build_integrity(outcome.build_id)
                    trace = await events.read_run(run_id)
                    stage_path = journal_path.parent / "stages" / f"{cell['cell_id']}.json"
                    record = _record(cell, outcome, trace, build, state_dir, stage_path)
                    # Reconstruct the frozen stage before writing the immutable record.
                    oracle = StateOracleStore(runtime.state)._load_ground_truth(build.build_id)
                    fixture = json.loads(
                        (runtime.state.build_dir(build.build_id) / "fixture.json").read_bytes()
                    )
                    row = await analysis.extract_checkpoint_assignment(
                        {**cell, "model_run_id": str(run_id)}, state_dir, events, oracle, fixture
                    )
                    if not row["trace_auditable"]:
                        raise ValueError("cell trace is unauditable; stop before any next cell")
                    record["stage_sha256"] = _sha256(
                        (json.dumps(row, sort_keys=True, indent=2) + "\n").encode()
                    )
                    trace_path = journal_path.parent / "traces" / f"{cell['cell_id']}.json"
                    record["trace_path"] = str(trace_path)
                    trace_raw = (
                        json.dumps([item.model_dump(mode="json") for item in trace], sort_keys=True)
                        + "\n"
                    ).encode()
                    if _write_bytes(trace_path, trace_raw) != record["trace_sha256"]:
                        raise ValueError("source trace serialization differs")
                    audited = await _audit_cell(
                        events, runtime.state, analysis, cell, record, stage_path
                    )
                    journal.append(record)
                    journal.completed[cell["cell_id"]] = record
                    records.append(record)
                    audits.append(audited)
                    pair_audits.append(audited)
                    committed_cost += Decimal(record["estimated_cost_usd"])
                    conservative_cost += (
                        Decimal(record["estimated_cost_usd"])
                        if record["resource_auditable"]
                        else CELL_RESERVATION
                    )
                    await _inventory(engine, events, records, audits)
                    print(
                        json.dumps(
                            {
                                "cell": cell["order"],
                                "status": record["status"],
                                "score_valid": record["score_valid"],
                                "estimated_cost_usd": str(committed_cost),
                                "conservative_cost_usd": str(conservative_cost),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    if guard.stopped or record["status"] not in {
                        "completed",
                        "budget_exhausted",
                        "agent_failed",
                        "provider_failed",
                    }:
                        raise ValueError("provider or run status requires study stop")
                    decision = _health_decision(records)
                    if decision != "continue":
                        raise ValueError(f"frozen provider-health gate: {decision}")
                _write_json(
                    journal_path.parent / f"pair-postcheck-{pair_index + 1}.json",
                    {
                        "protocol": "m661-feasibility-v1",
                        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                        "pair_number": pair_index + 1,
                        "cell_ids": [cell["cell_id"] for cell in pair],
                        "run_ids": [item["run_id"] for item in records[-2:]],
                        "audits": pair_audits,
                        "endpoint_preflight_sha256": endpoint_sha,
                        "cumulative_estimated_cost_usd": str(committed_cost),
                        "conservative_cost_usd": str(conservative_cost),
                    },
                )
            return {
                "completed": len(records),
                "planned": 96,
                "estimated_cost_usd": str(committed_cost),
                "conservative_cost_usd": str(conservative_cost),
            }
        finally:
            journal.close()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "execution-root",
        "source-root",
        "protocol-root",
        "manifest",
        "approval",
        "journal",
        "state-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        collect(
            args.execution_root.resolve(),
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
