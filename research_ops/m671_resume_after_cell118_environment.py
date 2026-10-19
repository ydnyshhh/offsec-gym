"""One new one-pass M6.7.1 launch for frozen cells 119 through 128 only.

Cells 109, 113, 116, and 118 stay missing; cell 109 keeps its unsettled reservation.
This operational wrapper never retries it, settles that reservation, or
changes the frozen source, protocol, manifest, assignments, or prior evidence.
"""

# Bind protocol modules after qualified-source import, as in m671_collect.py.
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import stat
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from m671_cell109_interrupted_stop import (
    INTERRUPTED_RUN_ID,
    UNSETTLED_CALL_ID,
    UNSETTLED_RESERVED_COST_MICROUSD,
    UNSETTLED_RESERVED_TOKENS,
    pinned_interrupted_trace,
    validate_known_reservation,
)
from m671_cell113_environment_stop import (
    FAILED_RUN_ID as CELL113_RUN_ID,
)
from m671_cell113_environment_stop import (
    pinned_failed_trace as pinned_cell113_trace,
)
from m671_cell118_environment_stop import (
    FAILED_RECEIPT_SHA256,
    FAILED_RUN_ID,
    PREFIX_PAIRS,
    STOPPED_FILE_SET_SHA256,
    STOPPED_JOURNAL_SHA256,
    STOPPED_LOG_SHA256,
    admit_cell118_stop,
    pinned_failed_trace,
    stopped_cell118_records,
)
from m671_cell118_recovery_postcheck import postcheck
from m671_collect import (
    CELL_CEILING,
    INPUT_PRICE,
    MANIFEST_SHA256,
    OUTPUT_PRICE,
    PROTOCOL_COMMIT,
    REVISION,
    SOURCE_COMMIT,
    UPSTREAM,
    EndpointGuard,
    cell_spec,
    create_json,
    endpoint_receipt,
    health_decision,
    inventory,
    now,
    pinned_inputs,
    sha,
)
from m671_resume_after_cell109_interruption import NEW_APPROVAL_SHA256 as CELL109_APPROVAL_SHA256
from m671_resume_after_cell113_environment import NEW_APPROVAL_SHA256 as CELL113_APPROVAL_SHA256
from m671_resume_after_cell116_environment import NEW_APPROVAL_SHA256 as CELL116_APPROVAL_SHA256
from m671_resume_after_precell_failure import NEW_APPROVAL_SHA256 as PREVIOUS_APPROVAL_SHA256
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.m67 import M67Arm
from offsecgym.research.m671_postcheck import _cell, trace_digest
from offsecgym.research.m671_protocol import CELL_COUNT, PROTOCOL, require_paid_approval
from offsecgym.research.m671_protocol import INPUT_PRICE as PROTOCOL_INPUT_PRICE
from offsecgym.research.m671_protocol import OUTPUT_PRICE as PROTOCOL_OUTPUT_PRICE
from offsecgym.research.m671_runner import ApprovedCell, M671CriticalFactorialRunner
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import ModelCallCompleted, ModelCallStarted, RunCompleted
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import model_reservations, run_usage

CONTINUATION_SCOPE = "m671_feasibility_cells_119_through_128_once_after_cell118_environment_failure"
FIRST_PAID_ORDER = 119
LAST_PAID_ORDER = 128
NEW_APPROVAL_SHA256 = "b3d198e9177e6896e6b05fdd8e2391a324d9f460d37829c97e8ed7c0d871a287"
OPERATIONAL_MERGE_COMMIT = "229fa8b5054afe54675d5868642f3eac5d302d13"
PREVIOUS_PIN_MERGE_COMMIT = "609d33ae85c29ed1b342832bf5619885ea4f6c8a"
QUALIFICATION_SHA256 = "8d086e9bdbd3a7e66c7c15e4e19efa1d36b51ee44ac435ef5f86bb974ec1e131"
STOPPED_AT = datetime.fromisoformat("2026-10-18T04:59:21+00:00")


def qualification_receipt(root: Path) -> None:
    """Bind the isolated no-model Docker qualification without changing its state."""
    path = root / "qualification.json"
    builds = list(root.glob("builds/*/manifest.json"))
    instances = list(root.glob("instances/*/manifest.json"))
    if (
        root.is_symlink()
        or path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
        or sha(path.read_bytes()) != QUALIFICATION_SHA256
        or len(builds) != 1
        or len(instances) != 1
        or any(item.is_symlink() or not item.is_file() for item in builds + instances)
    ):
        raise ValueError("isolated no-model qualification differs")
    receipt = json.loads(path.read_bytes())
    build = json.loads(builds[0].read_bytes())
    instance = json.loads(instances[0].read_bytes())
    expected = {
        "kind": "m671_cell118_isolated_no_model_range_qualification",
        "docker_context": "default",
        "observed_healthy": True,
        "cleanup_destroyed": True,
        "provider_calls": 0,
        "source_commit": SOURCE_COMMIT,
        "protocol_commit": PROTOCOL_COMMIT,
        "synthetic_seed": 900001,
        "build_manifest_sha256": sha(builds[0].read_bytes()),
        "instance_manifest_sha256": sha(instances[0].read_bytes()),
    }
    if (
        receipt != expected
        or instance.get("state") != "destroyed"
        or build.get("spec", {}).get("seed") != 900001
        or build.get("spec", {}).get("patched") is not False
    ):
        raise ValueError("isolated no-model qualification state differs")


def scoped_approval(path: Path, manifest: dict[str, object]) -> dict[str, object]:
    if NEW_APPROVAL_SHA256 == "0" * 64 or OPERATIONAL_MERGE_COMMIT == "0" * 40:
        raise ValueError("cell 118 recovery is closed until exact approval pin")
    if (
        path.name != "cell118-paid-approval.json"
        or path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
        or sha(path.read_bytes()) != NEW_APPROVAL_SHA256
    ):
        raise ValueError("cell 118 private approval bytes, path, or mode differ")
    approval = json.loads(path.read_bytes())
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    expected = {
        "approved_by": "human_user",
        "approved_for_paid_calls": True,
        "approval_scope": CONTINUATION_SCOPE,
        "protocol": PROTOCOL,
        "manifest_sha256": MANIFEST_SHA256,
        "assignment_sha256": manifest["assignment_sha256"],
        "source_commit": SOURCE_COMMIT,
        "protocol_commit": PROTOCOL_COMMIT,
        "operational_merge_commit": OPERATIONAL_MERGE_COMMIT,
        "consumed_continuation_approval_sha256": PREVIOUS_APPROVAL_SHA256,
        "consumed_cell109_approval_sha256": CELL109_APPROVAL_SHA256,
        "consumed_cell113_approval_sha256": CELL113_APPROVAL_SHA256,
        "consumed_cell116_approval_sha256": CELL116_APPROVAL_SHA256,
        "prior_execution_merge_commit": PREVIOUS_PIN_MERGE_COMMIT,
        "cell113_failed_run_id": str(CELL113_RUN_ID),
        "cell118_failed_run_id": str(FAILED_RUN_ID),
        "cell118_failure_receipt_sha256": FAILED_RECEIPT_SHA256,
        "cell118_stopped_journal_sha256": STOPPED_JOURNAL_SHA256,
        "cell118_stopped_log_sha256": STOPPED_LOG_SHA256,
        "cell118_stopped_file_set_sha256": STOPPED_FILE_SET_SHA256,
        "isolated_no_model_qualification_sha256": QUALIFICATION_SHA256,
        "interrupted_run_id": str(INTERRUPTED_RUN_ID),
        "unsettled_call_id": str(UNSETTLED_CALL_ID),
        "unsettled_reserved_tokens": UNSETTLED_RESERVED_TOKENS,
        "unsettled_reserved_cost_microusd": UNSETTLED_RESERVED_COST_MICROUSD,
        "retained_missing_orders": [109, 113, 116, 118],
        "retained_provider_failed_orders": [85, 86, 114, 117],
        "retained_budget_exhausted_orders": [115],
        "first_paid_cell_order": FIRST_PAID_ORDER,
        "last_paid_cell_order": LAST_PAID_ORDER,
        "paid_cell_ids": [cell["cell_id"] for cell in manifest["cells"][FIRST_PAID_ORDER - 1 :]],
        "paid_cells_authorized": LAST_PAID_ORDER - FIRST_PAID_ORDER + 1,
        "fresh_launch_count": 1,
        "no_retry_or_replacement": True,
        "retain_unsettled_reservation_without_mutation": True,
        "selected_endpoint": f"{UPSTREAM} | {REVISION}",
        "selected_revision": REVISION,
        "approved_cell_ids": [cell["cell_id"] for cell in manifest["cells"]],
        "approver": "human_user",
        "input_usd_per_million_tokens": str(INPUT_PRICE),
        "output_usd_per_million_tokens": str(OUTPUT_PRICE),
        "cumulative_estimated_cost_stop_usd": manifest["cumulative_estimated_cost_stop_usd"],
    }
    if (
        approved_at.tzinfo is None
        or approved_at <= STOPPED_AT
        or set(approval) != set(expected) | {"approved_at"}
        or any(approval.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("cell 118 approval does not bind exact scope and reservation")
    return approval


def pending_pairs(manifest: dict[str, object]) -> list[tuple[int, list[dict[str, object]]]]:
    pairs = []
    for pair_index in range(60, CELL_COUNT // 2 + 1):
        pair = manifest["cells"][2 * pair_index - 2 : 2 * pair_index]
        if (
            len(pair) != 2
            or pair[0]["pair_id"] != pair[1]["pair_id"]
            or {cell["variant"] for cell in pair} != {"vulnerable", "patched"}
        ):
            raise ValueError("frozen continuation pair differs")
        pairs.append((pair_index, pair))
    paid_orders = [
        cell["order"] for _, pair in pairs for cell in pair if cell["order"] >= FIRST_PAID_ORDER
    ]
    if paid_orders != list(range(FIRST_PAID_ORDER, LAST_PAID_ORDER + 1)):
        raise ValueError("continuation is not the exact frozen paid range")
    return pairs


def no_other_collector() -> None:
    output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or int(fields[0]) == os.getpid():
            continue
        executable = fields[1].split(None, 1)[0].rsplit("/", 1)[-1]
        if "python" in executable and any(
            name in fields[1]
            for name in (
                "m671_collect.py",
                "m671_resume_after_provider_pause.py",
                "m671_resume_after_precell_failure.py",
                "m671_resume_after_cell109_interruption.py",
                "m671_resume_after_cell113_environment.py",
                "m671_resume_after_cell116_environment.py",
                "m671_resume_after_cell118_environment.py",
            )
        ):
            raise ValueError("another M6.7.1 collector is running")


def precall_docker_gate() -> None:
    """Fail before cell_started when the pinned Docker daemon is unavailable."""
    if os.environ.get("DOCKER_CONTEXT") != "default":
        raise ValueError("dedicated Docker context differs")
    commands = (
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        ["docker", "compose", "version", "--short"],
        ["docker", "inspect", "--format", "{{.State.Running}}", "offsecgym-m671-feasibility-pg"],
    )
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("pre-call Docker health gate failed") from exc
        if result.returncode != 0 or not result.stdout.strip():
            raise ValueError("pre-call Docker health gate failed")
        if command[1] == "inspect" and result.stdout.strip().lower() != "true":
            raise ValueError("dedicated PostgreSQL container is not running")


async def known_reservation(engine: object) -> None:
    """Admit only the pinned old reservation; never settle or release it."""
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        active = (
            (
                await connection.execute(
                    select(model_reservations).where(model_reservations.c.settled_at.is_(None))
                )
            )
            .mappings()
            .all()
        )
        usage = (
            (
                await connection.execute(
                    select(run_usage).where(run_usage.c.run_id == INTERRUPTED_RUN_ID)
                )
            )
            .mappings()
            .one()
        )
        other_reserved_usage = (
            (
                await connection.execute(
                    select(run_usage.c.run_id).where(
                        run_usage.c.run_id != INTERRUPTED_RUN_ID,
                        (run_usage.c.reserved_tokens != 0)
                        | (run_usage.c.reserved_cost_microusd != 0),
                    )
                )
            )
            .scalars()
            .all()
        )
        await connection.rollback()
    validate_known_reservation(active, usage, other_reserved_usage)


def write_bytes(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def journal_header() -> dict[str, object]:
    return {
        "type": "batch_started",
        "protocol": PROTOCOL,
        "manifest_sha256": MANIFEST_SHA256,
        "continuation_scope": CONTINUATION_SCOPE,
        "cell118_stopped_journal_sha256": STOPPED_JOURNAL_SHA256,
        "approval_sha256": NEW_APPROVAL_SHA256,
        "operational_merge_commit": OPERATIONAL_MERGE_COMMIT,
    }


class ContinuationJournal:
    def __init__(self, path: Path) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, "w", encoding="utf-8")
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.append({**journal_header(), "at": now()})

    def append(self, value: dict[str, object]) -> None:
        self.stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self) -> None:
        self.stream.close()


async def continue_cells(args: argparse.Namespace) -> dict[str, object]:
    execution_root = args.execution_root.resolve()
    source_root = args.source_root.resolve()
    protocol_root = args.protocol_root.resolve()
    private = args.stopped_journal.parent.resolve()
    state_root = private / "range-state"
    continuation_root = private / "cell118-continuation"
    if (
        args.stopped_journal != private / "journal.jsonl"
        or args.stopped_log != private / "collector.log"
        or args.state_root != state_root
        or args.original_pairs != private / "pairs"
        or args.continuation_approval != private / "cell118-paid-approval.json"
        or args.continuation_root != continuation_root
        or continuation_root.exists()
        or continuation_root.is_symlink()
    ):
        raise ValueError("cell 118 continuation paths or create-once evidence differ")
    manifest_raw = pinned_inputs(
        execution_root, source_root, protocol_root, args.manifest.resolve()
    )
    for commit in (PREVIOUS_PIN_MERGE_COMMIT, OPERATIONAL_MERGE_COMMIT):
        if (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                cwd=execution_root,
                check=False,
            ).returncode
            != 0
        ):
            raise ValueError("reviewed cell 118 recovery merge is not an execution ancestor")
    manifest = json.loads(manifest_raw)
    selected_pairs = pending_pairs(manifest)
    qualification_receipt(private.parent / "m671-cell118-qualification")
    approval = scoped_approval(args.continuation_approval, manifest)
    if (
        INPUT_PRICE != PROTOCOL_INPUT_PRICE
        or OUTPUT_PRICE != PROTOCOL_OUTPUT_PRICE
        or CELL_COUNT != LAST_PAID_ORDER
        or not os.getenv("OPENROUTER_API_KEY")
        or not os.getenv("OFFSECGYM_DATABASE_URL")
    ):
        raise ValueError("private provider, database, price, or frozen cell count differs")
    no_other_collector()
    precall_docker_gate()
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    store = PostgresEventStore(engine)
    try:
        _cell117, _cell118, referenced, conservative = await admit_cell118_stop(
            manifest, private, state_root, engine
        )
        await known_reservation(engine)
        selected = endpoint_receipt()  # Fresh public no-key metadata before evidence creation.
        continuation_root.mkdir(mode=0o700)
        pairs_dir = continuation_root / "pairs"
        pairs_dir.mkdir(mode=0o700)
        previous_root = private / "cell116-continuation"
        previous_pairs = previous_root / "pairs"
        for pair_index in range(1, PREFIX_PAIRS + 1):
            prior = previous_pairs / f"pair-postcheck-{pair_index}.json"
            write_bytes(pairs_dir / prior.name, prior.read_bytes())
        # Preserve the already closed pair 58 preflight, then close pair 59
        # solely from the completed 117 trace and retained missing 118 trace.
        for pair_index in (58, 59):
            preflight = previous_root / f"endpoint-preflight-{pair_index}.json"
            write_bytes(continuation_root / preflight.name, preflight.read_bytes())
        retained, failed = stopped_cell118_records(manifest, private)
        pair = manifest["cells"][116:118]
        preflight = previous_root / "endpoint-preflight-59.json"
        create_json(
            pairs_dir / "pair-postcheck-59.json",
            {
                "protocol": PROTOCOL,
                "manifest_sha256": MANIFEST_SHA256,
                "pair_id": pair[0]["pair_id"],
                "cell_ids": [cell["cell_id"] for cell in pair],
                "run_ids": [retained["run_id"], failed["run_id"]],
                "endpoint_preflight_sha256": sha(preflight.read_bytes()),
                "conservative_cost_usd": str(conservative),
            },
        )
        create_json(
            continuation_root / "clearance.json",
            {
                "protocol": PROTOCOL,
                "manifest_sha256": MANIFEST_SHA256,
                "cell118_stopped_journal_sha256": STOPPED_JOURNAL_SHA256,
                "cell118_stopped_log_sha256": STOPPED_LOG_SHA256,
                "cell118_stopped_file_set_sha256": STOPPED_FILE_SET_SHA256,
                "cell118_failure_receipt_sha256": FAILED_RECEIPT_SHA256,
                "isolated_no_model_qualification_sha256": QUALIFICATION_SHA256,
                "approval_sha256": NEW_APPROVAL_SHA256,
                "operational_merge_commit": OPERATIONAL_MERGE_COMMIT,
                "first_paid_order": FIRST_PAID_ORDER,
                "last_paid_order": LAST_PAID_ORDER,
                "retained_missing_orders": [109, 113, 116, 118],
                "retained_unsettled_call_id": str(UNSETTLED_CALL_ID),
                "read_only_prefix_source_runs": len(referenced),
                "conservative_prefix_cost_usd": str(conservative),
                "fresh_endpoint": selected,
            },
        )
        journal = ContinuationJournal(continuation_root / "journal.jsonl")
        try:
            runtime = ComposeRangeRuntime(state_root)
            guard = EndpointGuard()  # New provider-health epoch starts at 119.
            later: list[dict[str, object]] = []
            for pair_index, pair in selected_pairs:
                endpoint_hash = create_json(
                    continuation_root / f"endpoint-preflight-{pair_index}.json", endpoint_receipt()
                )
                pair_runs: list[str | None] = []
                for cell in pair:
                    order = int(cell["order"])
                    if order != FIRST_PAID_ORDER + len(later):
                        raise ValueError("continuation is not in frozen order")
                    stopped_cell118_records(manifest, private)
                    approval = scoped_approval(args.continuation_approval, manifest)
                    if cell["cell_id"] not in approval["paid_cell_ids"]:
                        raise ValueError("cell is outside the new exact paid scope")
                    actual, active = await inventory(engine)
                    await pinned_interrupted_trace(store, state_root)
                    await pinned_cell113_trace(store, state_root)
                    await pinned_failed_trace(store)
                    await known_reservation(engine)
                    if actual != referenced or active != 1:
                        raise ValueError("authoritative run inventory or reservation differs")
                    # The frozen generic guard rejects any active reservation. The
                    # exact retained cell-109 reservation is admitted above and
                    # charged a full cell ceiling. No new active reservation passes.
                    require_paid_approval(
                        approval,
                        manifest_raw,
                        cell_id=cell["cell_id"],
                        started_cell_ids={row["cell_id"] for row in manifest["cells"][:118]}
                        | {row["cell_id"] for row in later},
                        active_reservations=active - 1,
                        cumulative_estimated_cost_usd=conservative,
                    )
                    if conservative + CELL_CEILING > Decimal(
                        manifest["cumulative_estimated_cost_stop_usd"]
                    ):
                        raise ValueError("next full-cell ceiling exceeds cumulative cap")
                    spec = cell_spec(source_root, cell)
                    arm = M67Arm(
                        provenance_reporting=bool(cell["A"]),
                        experiment_commitment=bool(cell["B"]),
                    )
                    precall_docker_gate()
                    run_id = uuid4()
                    journal.append(
                        {
                            "type": "cell_started",
                            "cell_id": cell["cell_id"],
                            "order": order,
                            "run_id": str(run_id),
                            "at": now(),
                        }
                    )
                    outcome = await M671CriticalFactorialRunner(
                        runtime,
                        store,
                        guard.provider(os.environ["OPENROUTER_API_KEY"]),
                        arm=arm,
                        admission=ApprovedCell(cell["cell_id"], cell["arm"], cell["checkpoint_id"]),
                    ).run(spec, run_id=run_id)
                    trace = await store.read_run(run_id)
                    terminal = [event for event in trace if isinstance(event, RunCompleted)]
                    if len(terminal) != 1 or terminal[0].sequence_number != len(trace):
                        raise ValueError("source terminal absent or not last")
                    if outcome.evaluation.status not in {
                        "completed",
                        "budget_exhausted",
                        "agent_failed",
                        "provider_failed",
                    }:
                        reason = outcome.failure_reason or "unknown"
                        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,80}", reason):
                            reason = "redacted"
                        create_json(
                            continuation_root / f"failed-cell-{order}.json",
                            {
                                "order": order,
                                "run_id": str(run_id),
                                "terminal_status": outcome.evaluation.status,
                                "failure_class": reason,
                                "trace_sha256": trace_digest(trace),
                                "model_call_starts": sum(
                                    isinstance(event, ModelCallStarted) for event in trace
                                ),
                            },
                        )
                        raise ValueError("unexpected terminal status")
                    receipt = state_root / "critical_checkpoints" / f"{run_id.hex}.json"
                    starts = [event for event in trace if isinstance(event, ModelCallStarted)]
                    completions = [
                        event for event in trace if isinstance(event, ModelCallCompleted)
                    ]
                    cost = sum(
                        (
                            Decimal(str(event.estimated_cost_usd))
                            for event in completions
                            if event.estimated_cost_usd is not None
                        ),
                        Decimal(0),
                    )
                    token_cost = sum(
                        (
                            (
                                Decimal(event.input_tokens) * INPUT_PRICE
                                + Decimal(event.output_tokens) * OUTPUT_PRICE
                            )
                            / Decimal(1_000_000)
                            for event in completions
                        ),
                        Decimal(0),
                    )
                    if abs(cost - token_cost) > Decimal("0.000001"):
                        raise ValueError("model cost differs from frozen token prices")
                    resource_auditable = len(starts) == len(completions) and all(
                        event.estimated_cost_usd is not None for event in completions
                    )
                    record = {
                        "type": "cell_completed",
                        "cell_id": cell["cell_id"],
                        "order": order,
                        "run_id": str(run_id),
                        "experiment_sha256": cell["experiment_sha256"],
                        "terminal_status": outcome.evaluation.status,
                        "failure_reason": outcome.failure_reason,
                        "evaluation": outcome.evaluation.model_dump(mode="json"),
                        "receipt_sha256": sha(receipt.read_bytes()),
                        "trace_sha256": trace_digest(trace),
                        "estimated_cost_usd": str(cost) if resource_auditable else None,
                        "at": now(),
                    }
                    row, source_ids = await _cell(
                        cell, record, events=store, state_root=state_root, manifest=manifest
                    )
                    if (
                        not row["trace_auditable"]
                        or row["score_valid"] != outcome.evaluation.score_valid
                        or row["resource_auditable"] != resource_auditable
                    ):
                        raise ValueError("immediate authoritative continuation replay failed")
                    referenced.update(source_ids)
                    actual, active = await inventory(engine)
                    await pinned_interrupted_trace(store, state_root)
                    await pinned_cell113_trace(store, state_root)
                    await pinned_failed_trace(store)
                    await known_reservation(engine)
                    if actual != referenced or active != 1:
                        raise ValueError("post-cell run inventory or reservation differs")
                    journal.append(record)
                    later.append(record)
                    pair_runs.append(str(run_id))
                    conservative += cost if resource_auditable else CELL_CEILING
                    if guard.stopped or health_decision(later) != "continue":
                        raise ValueError("provider-health or endpoint gate stopped continuation")
                    print(
                        json.dumps(
                            {
                                "cell": order,
                                "terminal_status": record["terminal_status"],
                                "conservative_cost_usd": str(conservative),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                create_json(
                    pairs_dir / f"pair-postcheck-{pair_index}.json",
                    {
                        "protocol": PROTOCOL,
                        "manifest_sha256": MANIFEST_SHA256,
                        "pair_id": pair[0]["pair_id"],
                        "cell_ids": [cell["cell_id"] for cell in pair],
                        "run_ids": pair_runs,
                        "endpoint_preflight_sha256": endpoint_hash,
                        "conservative_cost_usd": str(conservative),
                    },
                )
            header = json.loads((continuation_root / "journal.jsonl").read_bytes().splitlines()[0])
            if any(header.get(key) != value for key, value in journal_header().items()):
                raise ValueError("continuation header differs")
            result = await postcheck(
                manifest,
                private,
                continuation_root,
                state_root,
                engine,
                header=header,
                protocol_root=protocol_root,
            )
            duplicate = await postcheck(
                manifest,
                private,
                continuation_root,
                state_root,
                engine,
                header=header,
                protocol_root=protocol_root,
            )
            if result["analysis_bytes"] != duplicate["analysis_bytes"]:
                raise ValueError("read-only deterministic analysis differs")
            write_bytes(continuation_root / "frozen-analysis.json", result["analysis_bytes"])
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
        "continuation-approval",
        "stopped-journal",
        "stopped-log",
        "state-root",
        "original-pairs",
        "continuation-root",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    print(json.dumps(asyncio.run(continue_cells(parser.parse_args())), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
