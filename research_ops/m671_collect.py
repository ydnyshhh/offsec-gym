"""One-pass, approval-gated M6.7.1 collection over the frozen factorial order.

This operational wrapper is deliberately separate from the qualified source and
the frozen protocol. It does not create an approval, a manifest, or a database.
"""

# Import order is intentional: bind protocol-only modules after the qualified
# source package has been imported. Ruff must not move these imports earlier.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import importlib
import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

import offsecgym
import offsecgym.research

# In live execution, import the unchanged runtime from the qualified source
# checkout and only M6.7.1 research modules from the reviewed protocol checkout.
_protocol_src = os.environ.get("M671_PROTOCOL_SRC")
if _protocol_src:
    _research_path = Path(_protocol_src).resolve() / "offsecgym" / "research"
    if not _research_path.is_dir():
        raise ValueError("pinned M6.7.1 protocol module directory absent")
    offsecgym.research.__path__.append(str(_research_path))

from offsecgym.experiment.m67 import M67Arm
from offsecgym.providers.base import ModelTurn, ProviderFailure, ProviderRequestError
from offsecgym.providers.openrouter import OpenRouterResponsesProvider, selected_endpoint
from offsecgym.research.m671_postcheck import _cell, postcheck, trace_digest, verify_manifest
from offsecgym.research.m671_protocol import (
    CELL_COUNT,
    INPUT_PRICE,
    MANIFEST_PATH,
    OUTPUT_PRICE,
    PROTOCOL,
    PROTOCOL_PATHS,
    REVISION,
    SOURCE_COMMIT,
    SOURCE_PATHS,
    TOKEN_LIMIT,
    UPSTREAM,
    base_spec,
    digest,
    read_private_paid_approval,
    require_paid_approval,
)
from offsecgym.research.m671_runner import ApprovedCell, M671CriticalFactorialRunner
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import ModelCallCompleted, ModelCallStarted, RunCompleted
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import action_reservations, model_reservations, runs

PROTOCOL_COMMIT = "86c5406024e961eaaeacb851d9bf51d4a550df04"
MANIFEST_SHA256 = "d51ba9d9dffe12c6e809b32196f2c48937f72e33768ffb2ad050218d4be41e2f"
APPROVAL_SHA256 = "0" * 64  # A reviewed, separate approval-pin change must open collection.
ENDPOINT_URL = "https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints"
MODEL = "moonshotai/kimi-k3"
CELL_CEILING = Decimal(TOKEN_LIMIT) * max(INPUT_PRICE, OUTPUT_PRICE) / Decimal(1_000_000)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def clean(root: Path) -> None:
    if subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root, text=True
    ):
        raise ValueError("pinned checkout is dirty")


def pinned_inputs(
    execution_root: Path, source_root: Path, protocol_root: Path, manifest_path: Path
) -> bytes:
    if head(source_root) != SOURCE_COMMIT or head(protocol_root) != PROTOCOL_COMMIT:
        raise ValueError("qualified source or reviewed protocol checkout differs")
    for root in (execution_root, source_root, protocol_root):
        clean(root)
    if (
        Path(offsecgym.__file__).resolve()
        != (source_root / "src" / "offsecgym" / "__init__.py").resolve()
    ):
        raise ValueError("runtime imports are outside qualified source checkout")
    for name in ("m671_protocol", "m671_runner", "m671_postcheck", "m671_root_signals"):
        module = importlib.import_module(f"offsecgym.research.{name}")
        if (
            Path(module.__file__).resolve()
            != (protocol_root / "src" / "offsecgym" / "research" / f"{name}.py").resolve()
        ):
            raise ValueError(f"M6.7.1 {name} import is outside protocol checkout")
    if manifest_path != execution_root / MANIFEST_PATH or manifest_path.is_symlink():
        raise ValueError("manifest path differs from execution checkout")
    raw = manifest_path.read_bytes()
    if sha(raw) != MANIFEST_SHA256:
        raise ValueError("frozen manifest bytes differ")
    manifest = json.loads(raw)
    if manifest.get("protocol_commit") != PROTOCOL_COMMIT:
        raise ValueError("frozen protocol commit differs")
    for path in SOURCE_PATHS:
        expected = subprocess.check_output(
            ["git", "show", f"{SOURCE_COMMIT}:{path}"], cwd=protocol_root
        )
        if (
            (execution_root / path).read_bytes() != expected
            or (source_root / path).read_bytes() != expected
            or (protocol_root / path).read_bytes() != expected
        ):
            raise ValueError(f"qualified source drifted: {path}")
    for path in PROTOCOL_PATHS:
        expected = subprocess.check_output(
            ["git", "show", f"{PROTOCOL_COMMIT}:{path}"], cwd=protocol_root
        )
        if (execution_root / path).read_bytes() != expected or (
            protocol_root / path
        ).read_bytes() != expected:
            raise ValueError(f"frozen protocol drifted: {path}")
    verify_manifest(manifest, root=protocol_root)
    return raw


def private_approval(path: Path, manifest_raw: bytes) -> dict[str, object]:
    if APPROVAL_SHA256 == "0" * 64:
        raise ValueError("paid collection is closed until separate approval pin")
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("private approval receipt is missing or not mode 0600")
    raw = path.read_bytes()
    if sha(raw) != APPROVAL_SHA256:
        raise ValueError("private approval receipt bytes differ")
    approval = read_private_paid_approval(path)
    manifest = json.loads(manifest_raw)
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    frozen_at = datetime.fromisoformat(
        manifest["price_snapshot"]["checked_at"].replace("Z", "+00:00")
    )
    if approved_at.utcoffset() is None or approved_at < frozen_at:
        raise ValueError("approval predates frozen manifest")
    require_paid_approval(
        approval,
        manifest_raw,
        cell_id=manifest["cells"][0]["cell_id"],
        started_cell_ids=set(),
        active_reservations=0,
        cumulative_estimated_cost_usd=Decimal(0),
    )
    return approval


def endpoint_receipt() -> dict[str, object]:
    from urllib.request import urlopen

    with urlopen(ENDPOINT_URL, timeout=20) as response:
        raw = response.read(2_000_000)
    matches = [
        item
        for item in json.loads(raw)["data"]["endpoints"]
        if item.get("name") == f"{UPSTREAM} | {REVISION}" and item.get("provider_name") == UPSTREAM
    ]
    if len(matches) != 1:
        raise ValueError("selected endpoint absent or ambiguous")
    item = matches[0]
    if (
        item.get("model_id") != MODEL
        or item.get("status") != 0
        or Decimal(item["pricing"]["prompt"]) * 1_000_000 != INPUT_PRICE
        or Decimal(item["pricing"]["completion"]) * 1_000_000 != OUTPUT_PRICE
        or not {"tools", "tool_choice", "reasoning_effort"}
        <= set(item.get("supported_parameters", []))
    ):
        raise ValueError("selected endpoint, support, or price drifted")
    return {"checked_at": now(), "response_sha256": sha(raw), "endpoint": item["name"]}


class EndpointGuard:
    def __init__(self) -> None:
        self.stopped = False

    def provider(self, key: str) -> OpenRouterResponsesProvider:
        guard = self

        class Guarded(OpenRouterResponsesProvider):
            async def complete(self, request_payload: dict[str, object]) -> ModelTurn:
                if guard.stopped:
                    raise ProviderFailure("m671_provider_stopped")
                if (
                    request_payload.get("model") != MODEL
                    or request_payload.get("provider")
                    != {"order": ["moonshotai"], "allow_fallbacks": False}
                    or request_payload.get("reasoning") != {"effort": "high"}
                    or request_payload.get("store") is not False
                ):
                    guard.stopped = True
                    raise ProviderRequestError("m671_request_policy_drift")
                try:
                    turn = await super().complete(request_payload)
                    if selected_endpoint(turn.raw_response) != (REVISION, UPSTREAM):
                        guard.stopped = True
                        raise ProviderFailure(
                            "m671_selected_endpoint_drift", raw_response=turn.raw_response
                        )
                    return turn
                except ProviderRequestError:
                    guard.stopped = True
                    raise
                except ProviderFailure:
                    raise
                except Exception:
                    guard.stopped = True
                    raise

        return Guarded(key)


class Journal:
    def __init__(self, path: Path, manifest_sha256: str) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, "w", encoding="utf-8")
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.append(
            {
                "type": "batch_started",
                "protocol": PROTOCOL,
                "manifest_sha256": manifest_sha256,
                "at": now(),
            }
        )

    def append(self, value: dict[str, object]) -> None:
        self.stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self) -> None:
        self.stream.close()


def create_json(path: Path, value: object) -> str:
    raw = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return sha(raw)


async def inventory(engine: object) -> tuple[set[UUID], int]:
    async with engine.connect() as connection:
        run_ids = set((await connection.execute(select(runs.c.run_id))).scalars().all())
        actions = (
            (
                await connection.execute(
                    select(action_reservations.c.run_id).where(
                        action_reservations.c.released_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        models = (
            (
                await connection.execute(
                    select(model_reservations.c.run_id).where(
                        model_reservations.c.settled_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    return run_ids, len(actions) + len(models)


def cell_spec(root: Path, cell: dict[str, object]) -> ExperimentSpec:
    base = base_spec(root)
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
    arm = M67Arm(
        provenance_reporting=bool(cell["A"]),
        experiment_commitment=bool(cell["B"]),
    )
    if (
        arm.name != cell["arm"]
        or spec.seed != cell["seed"]
        or spec.range.seed != cell["seed"]
        or spec.range.patched != (cell["variant"] == "patched")
        or digest(["m671-run-v1", arm.run_hash(spec), cell["checkpoint_id"]])
        != cell["experiment_sha256"]
    ):
        raise ValueError("cell spec or arm differs from frozen assignment")
    return spec


def health_decision(records: list[dict[str, object]]) -> str:
    if not records or records[-1]["terminal_status"] != "provider_failed":
        return "continue"
    transient = {
        "provider_unavailable",
        "provider_timeout",
        "provider_server_error",
        "provider_rate_limited",
    }
    transport = transient - {"provider_rate_limited"}
    if records[-1].get("failure_reason") not in transient:
        return "stop"
    if sum(row["terminal_status"] == "provider_failed" for row in records[-10:]) >= 3:
        return "pause"
    if len(records) >= 2 and all(
        row["terminal_status"] == "provider_failed" and row.get("failure_reason") in transport
        for row in records[-2:]
    ):
        return "pause"
    return "continue"


async def collect(args: argparse.Namespace) -> dict[str, object]:
    execution_root = args.execution_root.resolve()
    source_root = args.source_root.resolve()
    protocol_root = args.protocol_root.resolve()
    manifest_path = args.manifest.resolve()
    approval_path = args.approval.resolve()
    journal_path = args.journal.resolve()
    state_root = args.state_root.resolve()
    pair_directory = args.pair_directory.resolve()
    manifest_raw = pinned_inputs(execution_root, source_root, protocol_root, manifest_path)
    manifest = json.loads(manifest_raw)
    approval = private_approval(approval_path, manifest_raw)
    if (
        journal_path.exists()
        or state_root.exists()
        or pair_directory.exists()
        or journal_path.is_symlink()
        or state_root.is_symlink()
        or pair_directory.is_symlink()
        or len(
            {journal_path.parent, state_root.parent, pair_directory.parent, approval_path.parent}
        )
        != 1
        or approval_path.is_relative_to(execution_root)
        or journal_path.is_relative_to(execution_root)
    ):
        raise ValueError("one-pass private evidence already exists or paths differ")
    key = os.getenv("OPENROUTER_API_KEY")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not key or not database_url:
        raise ValueError("private key and dedicated database URL required")
    engine = create_async_engine(database_url)
    events = PostgresEventStore(engine)
    try:
        run_ids, reservations = await inventory(engine)
        if run_ids or reservations:
            raise ValueError("dedicated PostgreSQL is not empty")
        endpoint_receipt()  # Public no-key check before creating any study evidence.
        journal = Journal(journal_path, MANIFEST_SHA256)
        try:
            runtime = ComposeRangeRuntime(state_root)
            guard = EndpointGuard()
            pair_directory.mkdir(mode=0o700)
            records: list[dict[str, object]] = []
            referenced_runs: set[UUID] = set()
            conservative = Decimal(0)
            for pair_index in range(CELL_COUNT // 2):
                pair = manifest["cells"][2 * pair_index : 2 * pair_index + 2]
                if pair[0]["pair_id"] != pair[1]["pair_id"] or {
                    item["variant"] for item in pair
                } != {"vulnerable", "patched"}:
                    raise ValueError("frozen pair differs")
                endpoint = endpoint_receipt()
                endpoint_hash = create_json(
                    journal_path.parent / f"endpoint-preflight-{pair_index + 1}.json", endpoint
                )
                pair_runs: list[str] = []
                for cell in pair:
                    actual_runs, active = await inventory(engine)
                    if actual_runs != referenced_runs or active:
                        raise ValueError("authoritative run inventory or reservation differs")
                    require_paid_approval(
                        approval,
                        manifest_raw,
                        cell_id=cell["cell_id"],
                        started_cell_ids={row["cell_id"] for row in records},
                        active_reservations=active,
                        cumulative_estimated_cost_usd=conservative,
                    )
                    if conservative + CELL_CEILING > Decimal(
                        manifest["cumulative_estimated_cost_stop_usd"]
                    ):
                        raise ValueError("next full-cell ceiling exceeds cumulative cap")
                    spec = cell_spec(source_root, cell)
                    admission = ApprovedCell(cell["cell_id"], cell["arm"], cell["checkpoint_id"])
                    arm = M67Arm(
                        provenance_reporting=bool(cell["A"]), experiment_commitment=bool(cell["B"])
                    )
                    run_id = uuid4()
                    journal.append(
                        {
                            "type": "cell_started",
                            "cell_id": cell["cell_id"],
                            "order": cell["order"],
                            "run_id": str(run_id),
                            "at": now(),
                        }
                    )
                    outcome = await M671CriticalFactorialRunner(
                        runtime, events, guard.provider(key), arm=arm, admission=admission
                    ).run(spec, run_id=run_id)
                    trace = await events.read_run(run_id)
                    terminal = [event for event in trace if isinstance(event, RunCompleted)]
                    if len(terminal) != 1 or terminal[0].sequence_number != len(trace):
                        raise ValueError("source terminal absent or not last")
                    if outcome.evaluation.status not in {
                        "completed",
                        "budget_exhausted",
                        "agent_failed",
                        "provider_failed",
                    }:
                        raise ValueError("unexpected terminal status")
                    receipt_path = state_root / "critical_checkpoints" / f"{run_id.hex}.json"
                    cost = sum(
                        (
                            Decimal(str(event.estimated_cost_usd))
                            for event in trace
                            if isinstance(event, ModelCallCompleted)
                            and event.estimated_cost_usd is not None
                        ),
                        Decimal(0),
                    )
                    starts = [event for event in trace if isinstance(event, ModelCallStarted)]
                    completions = [
                        event for event in trace if isinstance(event, ModelCallCompleted)
                    ]
                    token_cost = sum(
                        (
                            Decimal(event.input_tokens) * INPUT_PRICE
                            + Decimal(event.output_tokens) * OUTPUT_PRICE
                        )
                        / Decimal(1_000_000)
                        for event in completions
                    )
                    if abs(cost - token_cost) > Decimal("0.000001"):
                        raise ValueError("model cost differs from frozen token prices")
                    resource_auditable = len(starts) == len(completions) and all(
                        event.estimated_cost_usd is not None for event in completions
                    )
                    record = {
                        "type": "cell_completed",
                        "cell_id": cell["cell_id"],
                        "order": cell["order"],
                        "run_id": str(run_id),
                        "experiment_sha256": cell["experiment_sha256"],
                        "terminal_status": outcome.evaluation.status,
                        "failure_reason": outcome.failure_reason,
                        "evaluation": outcome.evaluation.model_dump(mode="json"),
                        "receipt_sha256": sha(receipt_path.read_bytes()),
                        "trace_sha256": trace_digest(trace),
                        "estimated_cost_usd": str(cost) if resource_auditable else None,
                        "at": now(),
                    }
                    row, source_ids = await _cell(
                        cell, record, events=events, state_root=state_root, manifest=manifest
                    )
                    if (
                        not row["trace_auditable"]
                        or row["score_valid"] != outcome.evaluation.score_valid
                        or row["resource_auditable"] != resource_auditable
                    ):
                        raise ValueError("immediate authoritative cell replay failed")
                    referenced_runs.update(source_ids)
                    actual_runs, active = await inventory(engine)
                    if actual_runs != referenced_runs or active:
                        raise ValueError("post-cell run inventory or reservation differs")
                    journal.append(record)
                    records.append(record)
                    pair_runs.append(str(run_id))
                    conservative += cost if resource_auditable else CELL_CEILING
                    if guard.stopped or health_decision(records) != "continue":
                        raise ValueError("provider-health or endpoint gate stopped collection")
                    print(
                        json.dumps(
                            {
                                "cell": cell["order"],
                                "terminal_status": record["terminal_status"],
                                "conservative_cost_usd": str(conservative),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                create_json(
                    pair_directory / f"pair-postcheck-{pair_index + 1}.json",
                    {
                        "protocol": PROTOCOL,
                        "manifest_sha256": MANIFEST_SHA256,
                        "pair_id": pair[0]["pair_id"],
                        "cell_ids": [item["cell_id"] for item in pair],
                        "run_ids": pair_runs,
                        "endpoint_preflight_sha256": endpoint_hash,
                        "conservative_cost_usd": str(conservative),
                    },
                )
            result = await postcheck(
                manifest,
                journal_path.read_bytes(),
                MANIFEST_SHA256,
                pair_directory,
                state_root,
                events,
                engine,
                protocol_root=protocol_root,
            )
            return {key: value for key, value in result.items() if key != "analysis_bytes"}
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
        "state-root",
        "pair-directory",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    print(json.dumps(asyncio.run(collect(parser.parse_args())), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
