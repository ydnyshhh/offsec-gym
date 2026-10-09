"""Collect an explicitly approved M6.6 confirmatory sample from pinned checkouts.

This script remains closed until a post-freeze approval artifact and its exact
manifest hash are pinned below. Run with PYTHONPATH at the source checkout.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen

import yaml
from m66_confirmatory_audit import audit_cell, audit_pair_inventory
from m66_pilot_coverage import close_completed_monolithic_coverage
from sqlalchemy.ext.asyncio import create_async_engine

import offsecgym
from offsecgym.experiment.bootstrapped_monolithic import BootstrappedMonolithicExperimentRunner
from offsecgym.experiment.scripted import experiment_hash
from offsecgym.experiment.witness_planning import WitnessPlanningExperimentRunner
from offsecgym.providers.base import ModelTurn, ProviderFailure, ProviderRequestError
from offsecgym.providers.openrouter import OpenRouterResponsesProvider, selected_endpoint
from offsecgym.research.m64_execute import Journal, _sha256, _utc_now, _write_trace
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import (
    ActionRequested,
    ModelCallCompleted,
    ModelCallStarted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import model_tools
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events

EXPECTED_MANIFEST_SHA256 = "a8aa98654360945ad69f350c38153dccaf827434a4e157819f558ae8da05916c"
EXPECTED_APPROVAL_SHA256 = "638ab038ede1d8806068cc311c1d88c96fec9ba115a9647843864d4af3929848"
EXPECTED_PAUSE_APPROVAL_SHA256 = "5da9c5502d26e94adb1bf6b823563db62e178c2cb151a7d6f27d3b4b4cb81018"
EXPECTED_RATE_PAUSE_APPROVAL_SHA256 = (
    "190d124b3439d5cf78a88f6aa1749993bbd13c2f8e51920237ae81eb935497c2"
)
PROVIDER_PAUSE_STOP_JOURNAL_SHA256 = (
    "1f47e18f9f8b993ea6ddcae3a240133235da4d9b904653b8c5dc932d681acf1d"
)
RATE_PAUSE_STOP_JOURNAL_SHA256 = "bc651787966bbbfbdcc750a6949272f9cb8c1fc25f064ae7c75a51126480b87b"
RATE_PAUSE_CLEARANCE_NAME = "provider-rate-pause-clearance-after-cell-188.json"
EXPECTED_SOURCE_COMMIT = "6bfb14dc240ae6ab7e65bd04025b74688312a806"
EXPECTED_PROTOCOL = "m66-confirmatory-v2"
ENDPOINT_URL = "https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints"
PRICE_IN = Decimal("0.000003")
PRICE_OUT = Decimal("0.000015")
MAX_CELL_TOKENS = 120_000
CELL_WORST_USD = Decimal(MAX_CELL_TOKENS) * PRICE_OUT


def _health_module(protocol_root: Path):
    path = protocol_root / "src/offsecgym/research/m66_confirmatory_attrition.py"
    spec = importlib.util.spec_from_file_location("_m66_confirmatory_attrition", path)
    if spec is None or spec.loader is None:
        raise ValueError("frozen provider-health policy cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git_head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _require_clean_checkout(root: Path) -> None:
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root, text=True
    )
    if status:
        raise ValueError("pinned checkout has local or untracked changes")


def _source_bytes(root: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=root)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _execution_approval(manifest: dict, approval_bytes: bytes) -> dict:
    if EXPECTED_MANIFEST_SHA256 == "0" * 64 or EXPECTED_APPROVAL_SHA256 == "0" * 64:
        raise ValueError("confirmatory paid collection is closed pending exact manifest approval")
    if len(EXPECTED_APPROVAL_SHA256) != 64 or any(
        char not in "0123456789abcdef" for char in EXPECTED_APPROVAL_SHA256
    ):
        raise ValueError("confirmatory approval hash is not pinned")
    if _sha256(approval_bytes) != EXPECTED_APPROVAL_SHA256:
        raise ValueError("post-freeze approval artifact hash differs")
    approval = json.loads(approval_bytes)
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    if approved_at.utcoffset() is None:
        raise ValueError("confirmatory approval timestamp needs a timezone")
    frozen_at = datetime.fromisoformat(
        manifest["price_snapshot"]["checked_at"].replace("Z", "+00:00")
    )
    if approved_at < frozen_at:
        raise ValueError("confirmatory approval must follow the frozen manifest price check")
    if (
        approval.get("protocol") != EXPECTED_PROTOCOL
        or approval.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("source_commit") != EXPECTED_SOURCE_COMMIT
        or approval.get("protocol_commit") != manifest.get("protocol_commit")
        or approval.get("approved_for_paid_calls") is not True
        or approval.get("approval_scope") != "m66_confirmatory_280_cells_only"
        or Decimal(str(approval.get("cost_ceiling_usd")))
        != Decimal(str(manifest["cumulative_estimated_cost_stop_usd"]))
        or manifest.get("paid_model_calls_authorized") is not False
    ):
        raise ValueError("approval does not authorize this exact confirmatory manifest")
    return approval


def _approval_and_manifest(
    source_root: Path, protocol_root: Path, manifest_path: Path, approval_path: Path
) -> tuple[dict, dict]:
    raw = manifest_path.read_bytes()
    if EXPECTED_MANIFEST_SHA256 == "0" * 64 or _sha256(raw) != EXPECTED_MANIFEST_SHA256:
        raise ValueError("frozen confirmatory manifest hash is unapproved or differs")
    manifest = json.loads(raw)
    manifest_commit = manifest.get("protocol_commit")
    if _git_head(source_root) != EXPECTED_SOURCE_COMMIT:
        raise ValueError("collection checkout is not the exact source_commit")
    if _git_head(protocol_root) != manifest_commit:
        raise ValueError("configuration checkout is not the exact protocol_commit")
    _require_clean_checkout(source_root)
    _require_clean_checkout(protocol_root)
    if not Path(offsecgym.__file__).resolve().is_relative_to((source_root / "src").resolve()):
        raise ValueError("imported runtime is outside the pinned source checkout")
    if not approval_path.is_file():
        raise ValueError("separate confirmatory approval artifact is absent")
    approval = _execution_approval(manifest, approval_path.read_bytes())
    if (
        manifest.get("protocol") != EXPECTED_PROTOCOL
        or manifest.get("source_commit") != EXPECTED_SOURCE_COMMIT
        or manifest.get("protocol_commit") != manifest_commit
        or manifest.get("planned_trajectories") != 280
        or manifest.get("no_retry_or_replacement") is not True
        or manifest.get("paid_model_calls_authorized") is not False
        or Decimal(str(manifest.get("cumulative_estimated_cost_stop_usd"))) > Decimal("504")
        or manifest["model_request"].get("allow_fallbacks") is not False
        or manifest["model_request"].get("upstream_provider") != "moonshotai"
        or manifest["model_request"].get("reasoning") != "high"
    ):
        raise ValueError("confirmatory manifest and post-freeze approval disagree")
    for path, digest in manifest["source_file_hashes"].items():
        if (
            _sha256((source_root / path).read_bytes()) != digest
            or _source_bytes(source_root, EXPECTED_SOURCE_COMMIT, path)
            != (source_root / path).read_bytes()
        ):
            raise ValueError(f"pinned runtime source differs: {path}")
    for path, digest in manifest["protocol_file_hashes"].items():
        if (
            _sha256((protocol_root / path).read_bytes()) != digest
            or _source_bytes(protocol_root, manifest_commit, path)
            != (protocol_root / path).read_bytes()
        ):
            raise ValueError(f"pinned protocol differs: {path}")
    for entry in manifest["configs"].values():
        path = entry["path"]
        if (
            _sha256((protocol_root / path).read_bytes()) != entry["sha256"]
            or _source_bytes(protocol_root, manifest_commit, path)
            != (protocol_root / path).read_bytes()
        ):
            raise ValueError(f"pinned config differs: {path}")
    cells = manifest["cells"]
    if len(cells) != 280 or len({cell["cell_id"] for cell in cells}) != 280:
        raise ValueError("frozen confirmatory manifest must have 280 distinct cells")
    if _sha256(_canonical(cells)) != manifest["schedule"]["cell_assignment_sha256"]:
        raise ValueError("frozen confirmatory assignment hash differs")
    schemas = {
        family: {
            arm: model_tools(structured=True, family=family, witness_planning=arm == "witness")
            for arm in ("control", "witness")
        }
        for family in ("saas", "enterprise_change_control_v1")
    }
    if _sha256(_canonical(schemas)) != manifest["tool_schema_sha256"]:
        raise ValueError("frozen confirmatory model tool schema differs")
    for first, second in zip(cells[::2], cells[1::2], strict=True):
        if (
            (first["range_family"], first["variant"], first["seed"])
            != (second["range_family"], second["variant"], second["seed"])
            or [first["arm"], second["arm"]] != first["arm_order"]
            or first["arm_order"] != second["arm_order"]
            or set(first["arm_order"]) != {"control", "witness"}
        ):
            raise ValueError("frozen confirmatory pair order or build identity differs")
    return manifest, approval


def _spec(protocol_root: Path, manifest: dict, cell: dict) -> ExperimentSpec:
    entry = manifest["configs"][cell["range_family"]]
    base = ExperimentSpec.model_validate(
        yaml.safe_load((protocol_root / entry["path"]).read_text())
    )
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
        spec.range.family != cell["range_family"]
        or spec.range.seed != cell["seed"]
        or experiment_hash(spec) != cell["experiment_sha256"]
        or spec.budget.max_total_tokens != MAX_CELL_TOKENS
        or spec.budget.max_model_calls != cell["max_model_calls"]
        or spec.budget.max_actions != cell["max_actions"]
        or spec.budget.max_http_requests != cell["max_http_requests"]
        or spec.model is None
        or spec.model.provider != "openrouter"
        or spec.model.name != "moonshotai/kimi-k3"
        or spec.model.upstream_provider != "moonshotai"
        or spec.model.reasoning != "high"
        or Decimal(str(spec.model.input_usd_per_million_tokens)) != Decimal("3")
        or Decimal(str(spec.model.output_usd_per_million_tokens)) != Decimal("15")
    ):
        raise ValueError("cell spec differs from the frozen manifest")
    return spec


def _fresh_endpoint_check(manifest: dict) -> dict:
    with urlopen(ENDPOINT_URL, timeout=20) as response:
        raw = response.read(2_000_000)
    payload = json.loads(raw)
    endpoints = payload["data"]["endpoints"]
    expected = manifest["expected_selected_endpoint"]
    name = f"{expected['upstream_provider']} | {expected['revision']}"
    selected = [item for item in endpoints if item.get("name") == name]
    if (
        len(selected) != 1
        or selected[0].get("model_id") != manifest["model_request"]["name"]
        or selected[0].get("provider_name") != expected["upstream_provider"]
        or selected[0].get("status") != 0
        or Decimal(selected[0]["pricing"]["prompt"]) != PRICE_IN
        or Decimal(selected[0]["pricing"]["completion"]) != PRICE_OUT
        or not {"tools", "tool_choice", "reasoning_effort"}
        <= set(selected[0].get("supported_parameters", []))
    ):
        raise ValueError("selected OpenRouter endpoint or price has drifted")
    return {
        "at": _utc_now(),
        "response_sha256": _sha256(raw),
        "endpoint": name,
        "model_id": selected[0]["model_id"],
        "provider_name": selected[0]["provider_name"],
        "status": selected[0]["status"],
        "input_usd_per_million": str(PRICE_IN * 1_000_000),
        "output_usd_per_million": str(PRICE_OUT * 1_000_000),
        "source": ENDPOINT_URL,
    }


class EndpointGuard:
    def __init__(self, expected: tuple[str, str]) -> None:
        self.expected = expected
        self.stopped = False

    def provider(self, api_key: str) -> OpenRouterResponsesProvider:
        guard = self

        class GuardedOpenRouter(OpenRouterResponsesProvider):
            async def complete(self, request_payload: dict[str, object]) -> ModelTurn:
                if guard.stopped:
                    raise ProviderFailure("confirmatory_provider_stopped")
                if (
                    request_payload.get("model") != "moonshotai/kimi-k3"
                    or request_payload.get("provider")
                    != {"order": ["moonshotai"], "allow_fallbacks": False}
                    or request_payload.get("reasoning") != {"effort": "high"}
                    or request_payload.get("store") is not False
                ):
                    guard.stopped = True
                    raise ProviderRequestError("confirmatory_request_policy_drift")
                try:
                    turn = await super().complete(request_payload)
                    if selected_endpoint(turn.raw_response) != guard.expected:
                        raise ProviderFailure(
                            "confirmatory_endpoint_drift", raw_response=turn.raw_response
                        )
                except ProviderFailure as exc:
                    if exc.reason_code == "confirmatory_endpoint_drift":
                        guard.stopped = True
                    raise
                except ProviderRequestError:
                    guard.stopped = True
                    raise
                except Exception:
                    guard.stopped = True
                    raise
                return turn

        return GuardedOpenRouter(api_key)


def _record(
    *,
    cell: dict,
    order: int,
    outcome: object,
    spec: ExperimentSpec,
    trace: list,
    build: object,
    trace_path: Path,
) -> dict:
    if not trace or [event.sequence_number for event in trace] != list(range(1, len(trace) + 1)):
        raise ValueError("run trace sequence is not contiguous")
    if any(event.run_id != outcome.run_id for event in trace):
        raise ValueError("run trace contains a foreign event identity")
    run_starts = [event for event in trace if isinstance(event, RunStarted)]
    if len(run_starts) != 1:
        raise ValueError("run trace lacks one start")
    expected_hash = (
        experiment_hash(spec)
        if cell["arm"] == "control"
        else hashlib.sha256(
            _canonical(
                {"spec": spec.model_dump(mode="json"), "policy": "m66-generic-temporal-witness-v1"}
            )
        ).hexdigest()
    )
    if run_starts[0].experiment_hash != expected_hash:
        raise ValueError("run start does not bind the expected arm policy")
    run_ends = [event for event in trace if isinstance(event, RunCompleted)]
    if len(run_ends) != 1:
        raise ValueError("run trace lacks one completion")
    ranges = [event for event in trace if isinstance(event, RangeStarted)]
    if len(ranges) != 1 or ranges[0].build_id != outcome.build_id:
        raise ValueError("run build differs from range start")
    bootstrap = [event for event in trace if isinstance(event, PrerequisiteBootstrapCompleted)]
    bootstrap_actions = [
        event
        for event in trace
        if isinstance(event, ActionRequested) and event.source_phase == "bootstrap"
    ]
    if (
        len(bootstrap) != 1
        or bootstrap[0].action_count != len(bootstrap_actions)
        or bootstrap[0].action_count > (32 if cell["range_family"] == "saas" else 40)
        or bootstrap[0].http_request_count > (32 if cell["range_family"] == "saas" else 40)
    ):
        raise ValueError("pilot bootstrap boundary differs")
    projection = project_controller_events(trace)
    if (
        projection.active_workers
        or projection.active_actions
        or projection.active_coverage
        or projection.model_reservations
        or any(hold.active for hold in projection.admission_holds.values())
    ):
        raise ValueError("pilot leaves controller reservations active")
    starts = [event for event in trace if isinstance(event, ModelCallStarted)]
    calls = [event for event in trace if isinstance(event, ModelCallCompleted)]
    if any(
        event.provider != "openrouter" or event.model != "moonshotai/kimi-k3" for event in starts
    ):
        raise ValueError("pilot model request drifted")
    if any(
        (event.resolved_model_revision, event.resolved_upstream_provider)
        != ("moonshotai/kimi-k3-20260715", "Moonshot AI")
        for event in calls
    ):
        raise ValueError("pilot selected endpoint drifted")
    input_tokens = sum(event.input_tokens for event in calls)
    output_tokens = sum(event.output_tokens for event in calls)
    if input_tokens + output_tokens > MAX_CELL_TOKENS or len(starts) > 20:
        raise ValueError("pilot model budget exceeded")
    estimated_cost = Decimal(input_tokens) * PRICE_IN + Decimal(output_tokens) * PRICE_OUT
    if any(event.estimated_cost_usd is None for event in calls):
        raise ValueError("v2 model turn omitted configured token-price accounting")
    event_cost = sum(Decimal(str(event.estimated_cost_usd)) for event in calls)
    if abs(event_cost - estimated_cost) > Decimal("0.000001"):
        raise ValueError("v2 event and collector token-price accounting differ")
    trace_sha256 = _write_trace(trace_path, trace)
    return {
        "type": "cell_completed",
        "cell_id": cell["cell_id"],
        "order": order,
        "at": _utc_now(),
        "run_id": str(outcome.run_id),
        "build_id": str(outcome.build_id),
        "pair_id": str(build.pair_id),
        "fixture_digest": build.artifact_digests.get("fixture.json"),
        "experiment_sha256": cell["experiment_sha256"],
        "arm": cell["arm"],
        "variant": cell["variant"],
        "range_family": cell["range_family"],
        "status": outcome.evaluation.status,
        "score_valid": outcome.evaluation.score_valid,
        "evaluation": outcome.evaluation.model_dump(mode="json"),
        "failure_reason": outcome.failure_reason,
        "model_calls": len(calls),
        "model_call_starts": len(starts),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": float(estimated_cost),
        "bootstrap_actions": bootstrap[0].action_count,
        "bootstrap_http_requests": bootstrap[0].http_request_count,
        "gateway_actions": len(
            [
                event
                for event in trace
                if isinstance(event, ActionRequested) and event.source_phase is None
            ]
        ),
        "duration_seconds": round(
            (run_ends[0].occurred_at - run_starts[0].occurred_at).total_seconds(), 3
        ),
        "trace_path": str(trace_path),
        "trace_sha256": trace_sha256,
    }


def _write_json_create_only(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return _sha256(path.read_bytes())


def _pair_receipt_path(journal_path: Path, pair_number: int) -> Path:
    return journal_path.parent / f"pair-postcheck-{pair_number}.json"


def _admit_provider_pause_clearance(
    *,
    health,
    history: tuple,
    completed_pairs: int,
    cells: list[dict],
    completed: dict[str, dict],
    manifest: dict,
    approval_path: Path,
    journal_path: Path,
    clearance_path: Path | None,
) -> tuple:
    """Start a new health epoch only at a separately reviewed exact stop."""
    decision = health.provider_health(history)
    if clearance_path is None:
        if decision != "continue":
            raise ValueError("provider-health gate requires a recorded operational review")
        return history
    if clearance_path.name == RATE_PAUSE_CLEARANCE_NAME:
        return _admit_rate_pause_clearance(
            decision=decision,
            history=history,
            completed_pairs=completed_pairs,
            cells=cells,
            completed=completed,
            manifest=manifest,
            approval_path=approval_path,
            journal_path=journal_path,
            clearance_path=clearance_path,
        )
    if (
        decision != "pause"
        or completed_pairs != 45
        or len(completed) != 90
        or len(history) != 90
        or _sha256(journal_path.read_bytes()) != PROVIDER_PAUSE_STOP_JOURNAL_SHA256
        or not clearance_path.is_file()
    ):
        raise ValueError("provider-pause clearance is not at the reconciled boundary")
    first, second = cells[88:90]
    first_record, second_record = completed[first["cell_id"]], completed[second["cell_id"]]
    pair_path = _pair_receipt_path(journal_path, 45)
    if not pair_path.is_file():
        raise ValueError("provider-pause clearance lacks the reconciled pair receipt")
    receipt = json.loads(clearance_path.read_bytes())
    endpoint = manifest["expected_selected_endpoint"]
    selected = receipt.get("selected_endpoint_recheck", {})
    if (
        receipt.get("protocol") != "m66-confirmatory-v2-provider-pause-clearance-v1"
        or EXPECTED_PAUSE_APPROVAL_SHA256 == "0" * 64
        or receipt.get("pause_approval_sha256") != EXPECTED_PAUSE_APPROVAL_SHA256
        or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or receipt.get("approval_sha256") != _sha256(approval_path.read_bytes())
        or receipt.get("stop_journal_sha256") != PROVIDER_PAUSE_STOP_JOURNAL_SHA256
        or receipt.get("pair45_receipt_sha256") != _sha256(pair_path.read_bytes())
        or receipt.get("provider_health_before") != "pause"
        or receipt.get("provider_health_epoch_start_order") != 91
        or receipt.get("failed_cell_ids") != [first["cell_id"], second["cell_id"]]
        or receipt.get("failed_run_ids") != [first_record["run_id"], second_record["run_id"]]
        or any(
            record["status"] != "provider_failed"
            or record["failure_reason"] != "provider_unavailable"
            or record["score_valid"] is not False
            for record in (first_record, second_record)
        )
        or selected.get("endpoint") != f"{endpoint['upstream_provider']} | {endpoint['revision']}"
        or selected.get("model_id") != manifest["model_request"]["name"]
        or selected.get("provider_name") != endpoint["upstream_provider"]
        or selected.get("status") != 0
        or Decimal(str(selected.get("input_usd_per_million"))) != Decimal("3")
        or Decimal(str(selected.get("output_usd_per_million"))) != Decimal("15")
    ):
        raise ValueError("provider-pause clearance differs from frozen evidence")
    return ()


def _admit_rate_pause_clearance(
    *,
    decision: str,
    history: tuple,
    completed_pairs: int,
    cells: list[dict],
    completed: dict[str, dict],
    manifest: dict,
    approval_path: Path,
    journal_path: Path,
    clearance_path: Path,
) -> tuple:
    """Admit exactly the retained cell-188 429 pause, once, after review."""
    if (
        decision != "pause"
        or completed_pairs != 94
        or len(completed) != 188
        or len(history) != 188
        or _sha256(journal_path.read_bytes()) != RATE_PAUSE_STOP_JOURNAL_SHA256
        or EXPECTED_RATE_PAUSE_APPROVAL_SHA256 == "0" * 64
        or not clearance_path.is_file()
    ):
        raise ValueError("rate-pause clearance is not at the reconciled boundary")
    failed_cells = cells[185:188]
    failed_records = [completed[cell["cell_id"]] for cell in failed_cells]
    pair_path = _pair_receipt_path(journal_path, 94)
    endpoint_path = journal_path.parent / "endpoint-preflight-94.json"
    recheck_path = journal_path.parent / "endpoint-recheck-cell-187.json"
    if not pair_path.is_file() or not endpoint_path.is_file() or not recheck_path.is_file():
        raise ValueError("rate-pause clearance lacks reconciled pair or route receipts")
    endpoint = manifest["expected_selected_endpoint"]
    preflight = json.loads(endpoint_path.read_bytes())
    recheck = json.loads(recheck_path.read_bytes())
    receipt = json.loads(clearance_path.read_bytes())
    selected = receipt.get("selected_endpoint_recheck", {})
    if (
        receipt.get("protocol") != "m66-confirmatory-v2-rate-pause-clearance-v1"
        or receipt.get("rate_pause_approval_sha256") != EXPECTED_RATE_PAUSE_APPROVAL_SHA256
        or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or receipt.get("approval_sha256") != _sha256(approval_path.read_bytes())
        or receipt.get("stop_journal_sha256") != RATE_PAUSE_STOP_JOURNAL_SHA256
        or receipt.get("pair94_receipt_sha256") != _sha256(pair_path.read_bytes())
        or receipt.get("provider_health_before") != "pause"
        or receipt.get("provider_health_epoch_start_order") != 189
        or receipt.get("failed_cell_ids") != [cell["cell_id"] for cell in failed_cells]
        or receipt.get("failed_run_ids") != [record["run_id"] for record in failed_records]
        or any(
            record["status"] != "provider_failed"
            or record["failure_reason"] != "provider_rate_limited"
            or record["score_valid"] is not False
            for record in failed_records
        )
        or any(
            preflight.get(field) != recheck.get(field)
            for field in (
                "endpoint",
                "model_id",
                "provider_name",
                "status",
                "input_usd_per_million",
                "output_usd_per_million",
            )
        )
        or selected.get("endpoint") != f"{endpoint['upstream_provider']} | {endpoint['revision']}"
        or selected.get("model_id") != manifest["model_request"]["name"]
        or selected.get("provider_name") != endpoint["upstream_provider"]
        or selected.get("status") != 0
        or Decimal(str(selected.get("input_usd_per_million"))) != Decimal("3")
        or Decimal(str(selected.get("output_usd_per_million"))) != Decimal("15")
    ):
        raise ValueError("rate-pause clearance differs from frozen evidence")
    return ()


async def collect(
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
    provider_pause_clearance: Path | None = None,
) -> dict:
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, approval_path
    )
    key = os.getenv("OPENROUTER_API_KEY")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not key or not database_url:
        raise ValueError("OPENROUTER_API_KEY and OFFSECGYM_DATABASE_URL are required")
    cap = Decimal(str(approval["cost_ceiling_usd"]))
    if Decimal(str(manifest["maximum_configured_estimated_cost_usd"])) > cap:
        raise ValueError("confirmatory worst-case bound exceeds approval")
    health = _health_module(protocol_root)
    endpoint = manifest["expected_selected_endpoint"]
    expected_endpoint = endpoint["revision"], endpoint["upstream_provider"]
    journal = Journal(
        journal_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=expected_endpoint,
        max_estimated_usd=float(approval["cost_ceiling_usd"]),
    )
    engine = create_async_engine(database_url)
    events = PostgresEventStore(engine)
    runtime = ComposeRangeRuntime(state_dir)
    guard = EndpointGuard(expected_endpoint)
    try:
        cells = manifest["cells"]
        completed_ids = set(journal.completed)
        if completed_ids - {cell["cell_id"] for cell in cells}:
            raise ValueError("journal contains an unknown confirmatory cell")
        for order, cell in enumerate(cells, 1):
            record = journal.completed.get(cell["cell_id"])
            if record is None:
                if any(later["cell_id"] in completed_ids for later in cells[order:]):
                    raise ValueError("confirmatory journal has a gap in frozen order")
                break
            trace_path = Path(record["trace_path"])
            if (
                record["order"] != order
                or record["experiment_sha256"] != cell["experiment_sha256"]
                or not trace_path.is_file()
                or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
            ):
                raise ValueError("existing confirmatory cell differs from the frozen trace")
        audits: list[dict] = []
        spent = Decimal(0)
        completed_pairs = 0
        for index in range(0, len(cells), 2):
            pair_cells = cells[index : index + 2]
            records = [journal.completed.get(cell["cell_id"]) for cell in pair_cells]
            if records == [None, None]:
                break
            if None in records:
                raise ValueError("partial pair requires authoritative event-store reconciliation")
            receipt_path = _pair_receipt_path(journal_path, index // 2 + 1)
            if not receipt_path.is_file():
                raise ValueError("completed pair lacks its authoritative postcheck receipt")
            receipt = json.loads(receipt_path.read_bytes())
            endpoint_path = journal_path.parent / f"endpoint-preflight-{index // 2 + 1}.json"
            if (
                receipt.get("protocol") != EXPECTED_PROTOCOL
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("pair_number") != index // 2 + 1
                or receipt.get("cell_ids") != [cell["cell_id"] for cell in pair_cells]
                or len(receipt.get("audits", [])) != 2
                or not endpoint_path.is_file()
                or receipt.get("endpoint_preflight_sha256") != _sha256(endpoint_path.read_bytes())
            ):
                raise ValueError("prior pair receipt differs from the frozen manifest")
            pair_audits = []
            for cell, record, prior_audit in zip(
                pair_cells, records, receipt["audits"], strict=True
            ):
                audited = await audit_cell(
                    events=events,
                    state=runtime.state,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    protocol_root=protocol_root,
                    state_dir=state_dir,
                    journal_dir=journal_path.parent,
                    cell=cell,
                    record=record,
                    spec=_spec(protocol_root, manifest, cell),
                    expected_stage_sha256=prior_audit["stage_sha256"],
                )
                if audited != prior_audit:
                    raise ValueError("prior pair receipt differs from authoritative replay")
                pair_audits.append(audited)
                spent += Decimal(str(record["estimated_cost_usd"]))
            if (
                receipt.get("audits") != pair_audits
                or receipt.get("run_ids") != [record["run_id"] for record in records]
                or receipt.get("cumulative_estimated_cost_usd") != str(spent)
            ):
                raise ValueError("prior pair receipt cost or run identity differs")
            audits.extend(pair_audits)
            completed_pairs += 1
        if any(cell["cell_id"] in journal.completed for cell in cells[2 * completed_pairs :]):
            raise ValueError("confirmatory journal has completed cells after a missing pair")
        await audit_pair_inventory(engine, events, journal.completed, audits)
        if spent > cap:
            raise ValueError("existing confirmatory cost exceeds approval")
        terminal_history = tuple(
            health.CellTerminal(record["status"], record.get("failure_reason"))
            for cell in cells[: 2 * completed_pairs]
            for record in [journal.completed[cell["cell_id"]]]
        )
        terminal_history = _admit_provider_pause_clearance(
            health=health,
            history=terminal_history,
            completed_pairs=completed_pairs,
            cells=cells,
            completed=journal.completed,
            manifest=manifest,
            approval_path=approval_path,
            journal_path=journal_path,
            clearance_path=provider_pause_clearance,
        )
        for pair_index in range(completed_pairs, len(cells) // 2):
            index = pair_index * 2
            first, second = cells[index : index + 2]
            if spent + 2 * CELL_WORST_USD > cap:
                raise ValueError("insufficient approved cost capacity for the next pair")
            spec = _spec(protocol_root, manifest, first)
            if _spec(protocol_root, manifest, second) != spec:
                raise ValueError("paired arms differ in their experiment spec")
            if _pair_receipt_path(journal_path, pair_index + 1).exists():
                raise ValueError("unmatched pair receipt exists before a new pair")
            # This live check is made immediately before the first paid arm of every pair.
            receipt = _fresh_endpoint_check(manifest)
            endpoint_sha256 = _write_json_create_only(
                journal_path.parent / f"endpoint-preflight-{pair_index + 1}.json", receipt
            )
            pair_records = []
            pair_audits = []
            for order, cell in enumerate((first, second), index + 1):
                if spent + CELL_WORST_USD > cap:
                    raise ValueError("insufficient approved cost capacity for the next cell")
                journal.append(
                    {
                        "type": "cell_started",
                        "cell_id": cell["cell_id"],
                        "order": order,
                        "at": _utc_now(),
                    }
                )
                runner_type = (
                    BootstrappedMonolithicExperimentRunner
                    if cell["arm"] == "control"
                    else WitnessPlanningExperimentRunner
                )
                outcome = await runner_type(runtime, events, guard.provider(key)).run(spec)
                if outcome.build_id is None:
                    raise ValueError("confirmatory cell has no build; reconcile its started row")
                build = runtime.state.verify_build_integrity(outcome.build_id)
                if build.pair_id is None or build.spec != spec.range:
                    raise ValueError("pair build or fixture binding differs")
                await close_completed_monolithic_coverage(events, outcome.run_id)
                trace = await events.read_run(outcome.run_id)
                trace_path = journal_path.parent / "traces" / f"{cell['cell_id']}.json"
                if trace_path.exists():
                    raise ValueError("confirmatory trace path already exists for a new cell")
                record = _record(
                    cell=cell,
                    order=order,
                    outcome=outcome,
                    spec=spec,
                    trace=trace,
                    build=build,
                    trace_path=trace_path,
                )
                journal.append(record)
                journal.completed[cell["cell_id"]] = record
                spent += Decimal(str(record["estimated_cost_usd"]))
                pair_records.append(record)
                print(
                    json.dumps(
                        {
                            "cell": order,
                            "cell_id": cell["cell_id"],
                            "status": record["status"],
                            "score_valid": record["score_valid"],
                            "estimated_cost_usd": record["estimated_cost_usd"],
                            "cumulative_estimated_cost_usd": float(spent),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                audited = await audit_cell(
                    events=events,
                    state=runtime.state,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    protocol_root=protocol_root,
                    state_dir=state_dir,
                    journal_dir=journal_path.parent,
                    cell=cell,
                    record=record,
                    spec=spec,
                )
                pair_audits.append(audited)
                audits.append(audited)
                await audit_pair_inventory(engine, events, journal.completed, audits)
                terminal_history += (
                    health.CellTerminal(record["status"], record.get("failure_reason")),
                )
                if spent > cap or guard.stopped:
                    raise ValueError(
                        "cost or selected endpoint guard stopped confirmatory collection"
                    )
                decision = health.provider_health(terminal_history)
                if decision != "continue":
                    raise ValueError(f"provider-health gate {decision}; no retry or replacement")
                if record["status"] == "provider_failed" and cell == first:
                    # Preserve the randomized partner after an isolated failure,
                    # but recheck the selected route and price before any new call.
                    _write_json_create_only(
                        journal_path.parent / f"endpoint-recheck-cell-{order}.json",
                        _fresh_endpoint_check(manifest),
                    )
            if len({record["run_id"] for record in pair_records}) != 2 or any(
                (record["build_id"], record["pair_id"], record["fixture_digest"])
                != (
                    pair_records[0]["build_id"],
                    pair_records[0]["pair_id"],
                    pair_records[0]["fixture_digest"],
                )
                for record in pair_records[1:]
            ):
                raise ValueError("confirmatory arms did not share one build, pair, and fixture")
            if spent > cap:
                raise ValueError("confirmatory cumulative estimated cost exceeded approval")
            _write_json_create_only(
                _pair_receipt_path(journal_path, pair_index + 1),
                {
                    "protocol": EXPECTED_PROTOCOL,
                    "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                    "pair_number": pair_index + 1,
                    "cell_ids": [first["cell_id"], second["cell_id"]],
                    "run_ids": [record["run_id"] for record in pair_records],
                    "audits": pair_audits,
                    "endpoint_preflight_sha256": endpoint_sha256,
                    "cumulative_estimated_cost_usd": str(spent),
                },
            )
        return {
            "completed": len(journal.completed),
            "planned": len(cells),
            "estimated_cost_usd": float(spent),
        }
    finally:
        await engine.dispose()
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        collect(
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
