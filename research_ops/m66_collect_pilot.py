"""Collect the approved, excluded M6.6 pilot from its pinned source checkout.

Run this script with PYTHONPATH pointing at the source checkout's src directory.
The operational script is not part of the frozen model or analysis protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen

import yaml
from sqlalchemy.ext.asyncio import create_async_engine

import offsecgym
from offsecgym.experiment.scripted import experiment_hash
from offsecgym.providers.base import ModelTurn, ProviderFailure, ProviderRequestError
from offsecgym.providers.openrouter import OpenRouterResponsesProvider, selected_endpoint
from offsecgym.research.m64_execute import Journal, _sha256, _utc_now, _write_trace
from offsecgym.research.m66_pair import WitnessPlanningPairRunner
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
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events

EXPECTED_MANIFEST_SHA256 = "0bda0f40d75c8a683752f20da0cbf09ea8a5e7d1cd39eaaf6c77725bac8aabd8"
EXPECTED_SOURCE_COMMIT = "950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d"
EXPECTED_PROTOCOL_COMMIT = "840992f760eae35e89ae4993f3283894e86b6fbe"
ENDPOINT_URL = "https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints"
PRICE_IN = Decimal("0.000003")
PRICE_OUT = Decimal("0.000015")
MAX_CELL_TOKENS = 120_000
CELL_WORST_USD = Decimal(MAX_CELL_TOKENS) * PRICE_OUT


def _git_head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _source_bytes(root: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=root)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _approval_and_manifest(
    source_root: Path, protocol_root: Path, manifest_path: Path, approval_path: Path
) -> tuple[dict, dict]:
    if _git_head(source_root) != EXPECTED_SOURCE_COMMIT:
        raise ValueError("collection checkout is not the exact source_commit")
    if _git_head(protocol_root) != EXPECTED_PROTOCOL_COMMIT:
        raise ValueError("configuration checkout is not the exact protocol_commit")
    if not Path(offsecgym.__file__).resolve().is_relative_to((source_root / "src").resolve()):
        raise ValueError("imported runtime is outside the pinned source checkout")
    raw = manifest_path.read_bytes()
    if _sha256(raw) != EXPECTED_MANIFEST_SHA256:
        raise ValueError("frozen pilot manifest hash differs")
    manifest = json.loads(raw)
    approval = json.loads(approval_path.read_text())
    if (
        manifest.get("protocol") != "m66-pilot-v1"
        or manifest.get("source_commit") != EXPECTED_SOURCE_COMMIT
        or manifest.get("protocol_commit") != EXPECTED_PROTOCOL_COMMIT
        or manifest.get("planned_trajectories") != 8
        or manifest.get("no_retry_or_replacement") is not True
        or manifest.get("paid_model_calls_authorized") is not False
        or approval.get("protocol") != manifest["protocol"]
        or approval.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("source_commit") != EXPECTED_SOURCE_COMMIT
        or approval.get("protocol_commit") != EXPECTED_PROTOCOL_COMMIT
        or approval.get("approved_for_paid_calls") is not True
        or Decimal(str(approval.get("cost_ceiling_usd"))) != Decimal("15")
        or Decimal(str(manifest.get("cumulative_estimated_cost_stop_usd"))) != Decimal("15")
    ):
        raise ValueError("pilot manifest and post-freeze approval disagree")
    for bundle in manifest["source_hashes"].values():
        for path, digest in bundle["files"].items():
            if (
                _sha256((source_root / path).read_bytes()) != digest
                or _source_bytes(source_root, EXPECTED_SOURCE_COMMIT, path)
                != (source_root / path).read_bytes()
            ):
                raise ValueError(f"pinned runtime source differs: {path}")
    for entry in manifest["configs"].values():
        path = entry["path"]
        if (
            _sha256((protocol_root / path).read_bytes()) != entry["sha256"]
            or _source_bytes(protocol_root, EXPECTED_PROTOCOL_COMMIT, path)
            != (protocol_root / path).read_bytes()
        ):
            raise ValueError(f"pinned config differs: {path}")
    cells = manifest["cells"]
    if len(cells) != 8 or len({cell["cell_id"] for cell in cells}) != 8:
        raise ValueError("frozen pilot must have eight distinct cells")
    for first, second in zip(cells[::2], cells[1::2], strict=True):
        if (
            (first["range_family"], first["variant"], first["seed"])
            != (second["range_family"], second["variant"], second["seed"])
            or [first["arm"], second["arm"]] != first["arm_order"]
            or first["arm_order"] != second["arm_order"]
            or set(first["arm_order"]) != {"control", "witness"}
        ):
            raise ValueError("frozen pair order or build identity differs")
    return manifest, approval


def _spec(protocol_root: Path, manifest: dict, cell: dict) -> ExperimentSpec:
    entry = manifest["configs"][cell["range_family"]]
    base = ExperimentSpec.model_validate(
        yaml.safe_load((protocol_root / entry["path"]).read_text())
    )
    spec = base.model_copy(
        update={"range": base.range.model_copy(update={"patched": cell["variant"] == "patched"})}
    )
    if (
        spec.range.family != cell["range_family"]
        or spec.range.seed != cell["seed"]
        or experiment_hash(spec) != cell["experiment_sha256"]
        or spec.budget.max_total_tokens != MAX_CELL_TOKENS
        or spec.budget.max_model_calls != cell["max_model_calls"]
        or spec.budget.max_actions != cell["max_actions"]
        or spec.budget.max_http_requests != cell["max_http_requests"]
    ):
        raise ValueError("cell spec differs from the frozen manifest")
    return spec


def _fresh_endpoint_check(manifest: dict) -> dict:
    with urlopen(ENDPOINT_URL, timeout=20) as response:
        payload = json.load(response)
    endpoints = payload["data"]["endpoints"]
    expected = manifest["expected_selected_endpoint"]
    name = f"{expected['upstream_provider']} | {expected['revision']}"
    selected = [item for item in endpoints if item.get("name") == name]
    if (
        len(selected) != 1
        or selected[0].get("model_id") != manifest["model_request"]["name"]
        or Decimal(selected[0]["pricing"]["prompt"]) != PRICE_IN
        or Decimal(selected[0]["pricing"]["completion"]) != PRICE_OUT
    ):
        raise ValueError("selected OpenRouter endpoint or price has drifted")
    return {
        "at": _utc_now(),
        "endpoint": name,
        "model_id": selected[0]["model_id"],
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
                    raise ProviderFailure("pilot_provider_stopped")
                try:
                    turn = await super().complete(request_payload)
                except (ProviderFailure, ProviderRequestError):
                    guard.stopped = True
                    raise
                if selected_endpoint(turn.raw_response) != guard.expected:
                    guard.stopped = True
                    raise ProviderFailure("pilot_endpoint_drift", raw_response=turn.raw_response)
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
        or bootstrap[0].action_count > 32
        or bootstrap[0].http_request_count > 32
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


async def collect(
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, approval_path
    )
    key = os.getenv("OPENROUTER_API_KEY")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not key or not database_url:
        raise ValueError("OPENROUTER_API_KEY and OFFSECGYM_DATABASE_URL are required")
    if Decimal(str(manifest["maximum_configured_estimated_cost_usd"])) > Decimal("15"):
        raise ValueError("pilot worst-case bound exceeds approval")
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
    runner = WitnessPlanningPairRunner(runtime, events, lambda: guard.provider(key))
    try:
        cells = manifest["cells"]
        completed_ids = set(journal.completed)
        if completed_ids - {cell["cell_id"] for cell in cells}:
            raise ValueError("journal contains an unknown pilot cell")
        for order, cell in enumerate(cells, 1):
            record = journal.completed.get(cell["cell_id"])
            if record is None:
                if any(later["cell_id"] in completed_ids for later in cells[order:]):
                    raise ValueError("pilot journal has a gap in frozen order")
                break
            trace_path = Path(record["trace_path"])
            if (
                record["order"] != order
                or record["experiment_sha256"] != cell["experiment_sha256"]
                or not trace_path.is_file()
                or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
            ):
                raise ValueError("existing pilot cell differs from the frozen trace")
        spent = sum(Decimal(str(item["estimated_cost_usd"])) for item in journal.completed.values())
        for index in range(0, len(cells), 2):
            first, second = cells[index : index + 2]
            if first["cell_id"] in journal.completed and second["cell_id"] in journal.completed:
                continue
            if first["cell_id"] in journal.completed or second["cell_id"] in journal.completed:
                raise ValueError("partial pair requires event-store reconciliation")
            if spent + 2 * CELL_WORST_USD > Decimal("15"):
                raise ValueError("insufficient approved cost capacity for the next pair")
            spec = _spec(protocol_root, manifest, first)
            if _spec(protocol_root, manifest, second) != spec:
                raise ValueError("paired arms differ in their experiment spec")
            receipt = _fresh_endpoint_check(manifest)
            path = journal_path.parent / f"endpoint-preflight-{index // 2 + 1}.json"
            with path.open("x", encoding="utf-8") as handle:
                json.dump(receipt, handle, sort_keys=True, indent=2)
                handle.write("\n")
            for offset, cell in enumerate((first, second), index + 1):
                journal.append(
                    {
                        "type": "cell_started",
                        "cell_id": cell["cell_id"],
                        "order": offset,
                        "at": _utc_now(),
                    }
                )
            pair = await runner.run_pair(spec, arm_order=(first["arm"], second["arm"]))
            build = runtime.state.verify_build_integrity(pair.build_id)
            if build.pair_id is None or build.spec != spec.range:
                raise ValueError("pair build or fixture binding differs")
            for offset, cell in enumerate((first, second), index + 1):
                outcome = pair.control if cell["arm"] == "control" else pair.witness
                trace = await events.read_run(outcome.run_id)
                record = _record(
                    cell=cell,
                    order=offset,
                    outcome=outcome,
                    spec=spec,
                    trace=trace,
                    build=build,
                    trace_path=journal_path.parent / "traces" / f"{cell['cell_id']}.json",
                )
                journal.append(record)
                journal.completed[cell["cell_id"]] = record
                spent += Decimal(str(record["estimated_cost_usd"]))
                print(
                    json.dumps(
                        {
                            "cell": offset,
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
            if spent > Decimal("15"):
                raise ValueError("pilot cumulative estimated cost exceeded approval")
            if guard.stopped or any(
                not journal.completed[cell["cell_id"]]["score_valid"] for cell in (first, second)
            ):
                raise ValueError("pilot retained an invalid pair without retry; investigate")
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
