"""One-pass M6.7.1 continuation for only frozen cells 87 through 128.

The first 86 outcomes, including provider failures 85 and 86, are retained.
This wrapper uses the qualified source and frozen protocol without changing
either, and requires a separately pinned private exact-scope paid approval.
"""

# Bind protocol modules after qualified-source import, as in m671_collect.py.
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import stat
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from m671_collect import (
    APPROVAL_SHA256 as ORIGINAL_APPROVAL_SHA256,
)
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
    private_approval,
    sha,
)
from m671_provider_pause_stop import (
    STOP_COMPLETED,
    STOP_CONSERVATIVE_COST,
    STOP_EVENTS,
    STOP_JOURNAL_SHA256,
    STOP_LOG_SHA256,
    STOP_PAIRS,
    STOP_PAIRS_SHA256,
    STOP_PREFLIGHTS_SHA256,
    STOP_RUNS,
    admit_stopped_prefix,
    file_set_digest,
    stopped_records,
)
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.m67 import M67Arm
from offsecgym.research.m671_postcheck import _cell, postcheck, trace_digest
from offsecgym.research.m671_protocol import (
    CELL_COUNT,
    PROTOCOL,
    require_paid_approval,
)
from offsecgym.research.m671_protocol import (
    INPUT_PRICE as PROTOCOL_INPUT_PRICE,
)
from offsecgym.research.m671_protocol import (
    OUTPUT_PRICE as PROTOCOL_OUTPUT_PRICE,
)
from offsecgym.research.m671_runner import ApprovedCell, M671CriticalFactorialRunner
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import ModelCallCompleted, ModelCallStarted, RunCompleted
from offsecgym.storage.event_store import PostgresEventStore

CONTINUATION_SCOPE = "m671_feasibility_cells_87_through_128_once_after_provider_pause"
FIRST_PAID_ORDER = 87
LAST_PAID_ORDER = 128
NEW_APPROVAL_SHA256 = "0" * 64
OPERATIONAL_MERGE_COMMIT = "0" * 40
STOPPED_AT = datetime.fromisoformat("2026-10-16T01:51:35.300103+00:00")


def scoped_approval(path: Path, manifest: dict[str, object]) -> dict[str, object]:
    if NEW_APPROVAL_SHA256 == "0" * 64 or OPERATIONAL_MERGE_COMMIT == "0" * 40:
        raise ValueError("provider-pause continuation is closed until exact approval pin")
    if (
        path.name != "provider-pause-paid-approval.json"
        or path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
        or sha(path.read_bytes()) != NEW_APPROVAL_SHA256
    ):
        raise ValueError("provider-pause private approval bytes, path, or mode differ")
    approval = json.loads(path.read_bytes())
    approved_at = datetime.fromisoformat(
        str(approval.get("approved_at", "")).replace("Z", "+00:00")
    )
    allowed = [cell["cell_id"] for cell in manifest["cells"][STOP_COMPLETED:]]
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
        "prior_paid_approval_sha256": ORIGINAL_APPROVAL_SHA256,
        "stopped_journal_sha256": STOP_JOURNAL_SHA256,
        "stopped_log_sha256": STOP_LOG_SHA256,
        "stopped_pairs_sha256": STOP_PAIRS_SHA256,
        "stopped_preflights_sha256": STOP_PREFLIGHTS_SHA256,
        "stopped_completed_cells": STOP_COMPLETED,
        "stopped_source_runs": STOP_RUNS,
        "stopped_events": STOP_EVENTS,
        "stopped_pair_receipts": STOP_PAIRS,
        "retained_provider_failed_orders": [85, 86],
        "first_paid_cell_order": FIRST_PAID_ORDER,
        "last_paid_cell_order": LAST_PAID_ORDER,
        "paid_cell_ids": allowed,
        "paid_cells_authorized": LAST_PAID_ORDER - FIRST_PAID_ORDER + 1,
        "provider_health_epoch_start_order": FIRST_PAID_ORDER,
        "no_retry_or_replacement": True,
        "selected_endpoint": f"{UPSTREAM} | {REVISION}",
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
        raise ValueError("provider-pause approval does not bind exact stopped scope")
    return approval


def no_other_collector() -> None:
    output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or int(fields[0]) == os.getpid():
            continue
        executable = fields[1].split(None, 1)[0].rsplit("/", 1)[-1]
        if "python" in executable and any(
            name in fields[1] for name in ("m671_collect.py", "m671_resume_after_provider_pause.py")
        ):
            raise ValueError("another M6.7.1 collector is running")


def combined_journal(stopped: bytes, later: bytes) -> bytes:
    lines = later.splitlines(keepends=True)
    if not lines:
        raise ValueError("continuation journal is absent")
    header = json.loads(lines[0])
    if (
        header.get("type") != "batch_started"
        or header.get("protocol") != PROTOCOL
        or header.get("manifest_sha256") != MANIFEST_SHA256
        or header.get("continuation_scope") != CONTINUATION_SCOPE
        or header.get("stopped_journal_sha256") != STOP_JOURNAL_SHA256
        or header.get("approval_sha256") != NEW_APPROVAL_SHA256
        or header.get("operational_merge_commit") != OPERATIONAL_MERGE_COMMIT
    ):
        raise ValueError("continuation journal header differs")
    if not stopped.endswith(b"\n"):
        raise ValueError("stopped journal is not newline terminated")
    return stopped + b"".join(lines[1:])


def write_bytes(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class ContinuationJournal:
    def __init__(self, path: Path) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, "w", encoding="utf-8")
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.append(
            {
                "type": "batch_started",
                "protocol": PROTOCOL,
                "manifest_sha256": MANIFEST_SHA256,
                "continuation_scope": CONTINUATION_SCOPE,
                "stopped_journal_sha256": STOP_JOURNAL_SHA256,
                "approval_sha256": NEW_APPROVAL_SHA256,
                "operational_merge_commit": OPERATIONAL_MERGE_COMMIT,
                "at": now(),
            }
        )

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
    manifest_path = args.manifest.resolve()
    private = args.stopped_journal.parent.resolve()
    stopped_journal = private / "journal.jsonl"
    state_root = private / "range-state"
    stopped_pairs = private / "pairs"
    continuation_root = private / "provider-pause-continuation"
    if (
        args.stopped_journal != stopped_journal
        or args.stopped_log != private / "collector.log"
        or args.state_root != state_root
        or args.original_pairs != stopped_pairs
        or args.original_approval != private / "paid-approval.json"
        or args.continuation_approval != private / "provider-pause-paid-approval.json"
        or args.continuation_root != continuation_root
        or any(path.is_symlink() for path in (stopped_journal, state_root, stopped_pairs))
        or continuation_root.exists()
        or continuation_root.is_symlink()
    ):
        raise ValueError("provider-pause paths or create-once evidence differ")
    manifest_raw = pinned_inputs(execution_root, source_root, protocol_root, manifest_path)
    manifest = json.loads(manifest_raw)
    original_approval = private_approval(args.original_approval, manifest_raw)
    scoped_approval(args.continuation_approval, manifest)
    if (
        INPUT_PRICE != PROTOCOL_INPUT_PRICE
        or OUTPUT_PRICE != PROTOCOL_OUTPUT_PRICE
        or CELL_COUNT != LAST_PAID_ORDER
        or not os.getenv("OPENROUTER_API_KEY")
        or not os.getenv("OFFSECGYM_DATABASE_URL")
    ):
        raise ValueError("private provider, database, price, or frozen cell count differs")
    no_other_collector()
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    store = PostgresEventStore(engine)
    try:
        old_records, referenced = await admit_stopped_prefix(manifest, private, state_root, engine)
        selected = endpoint_receipt()  # Fresh public no-key metadata before evidence creation.
        continuation_root.mkdir(mode=0o700)
        pairs_dir = continuation_root / "pairs"
        pairs_dir.mkdir(mode=0o700)
        for pair_index in range(1, STOP_PAIRS + 1):
            source = stopped_pairs / f"pair-postcheck-{pair_index}.json"
            write_bytes(pairs_dir / source.name, source.read_bytes())
        if file_set_digest(sorted(pairs_dir.glob("pair-postcheck-*.json"))) != STOP_PAIRS_SHA256:
            raise ValueError("copied stopped pair receipts differ byte for byte")
        pair43 = manifest["cells"][84:86]
        old_preflight = private / "endpoint-preflight-43.json"
        create_json(
            pairs_dir / "pair-postcheck-43.json",
            {
                "protocol": PROTOCOL,
                "manifest_sha256": MANIFEST_SHA256,
                "pair_id": pair43[0]["pair_id"],
                "cell_ids": [cell["cell_id"] for cell in pair43],
                "run_ids": [row["run_id"] for row in old_records[84:86]],
                "endpoint_preflight_sha256": sha(old_preflight.read_bytes()),
                "conservative_cost_usd": str(STOP_CONSERVATIVE_COST),
            },
        )
        create_json(
            continuation_root / "clearance.json",
            {
                "protocol": PROTOCOL,
                "manifest_sha256": MANIFEST_SHA256,
                "stopped_journal_sha256": STOP_JOURNAL_SHA256,
                "stopped_log_sha256": STOP_LOG_SHA256,
                "stopped_pairs_sha256": STOP_PAIRS_SHA256,
                "prior_paid_approval_sha256": ORIGINAL_APPROVAL_SHA256,
                "continuation_approval_sha256": NEW_APPROVAL_SHA256,
                "operational_merge_commit": OPERATIONAL_MERGE_COMMIT,
                "first_paid_order": FIRST_PAID_ORDER,
                "last_paid_order": LAST_PAID_ORDER,
                "fresh_endpoint": selected,
                "read_only_prefix_source_runs": len(referenced),
            },
        )
        journal_path = continuation_root / "journal.jsonl"
        journal = ContinuationJournal(journal_path)
        try:
            runtime = ComposeRangeRuntime(state_root)
            guard = EndpointGuard()  # One new health epoch starts at cell 87.
            later: list[dict[str, object]] = []
            conservative = STOP_CONSERVATIVE_COST
            for pair_index in range(STOP_PAIRS + 1, CELL_COUNT // 2 + 1):
                pair = manifest["cells"][2 * pair_index - 2 : 2 * pair_index]
                if pair[0]["pair_id"] != pair[1]["pair_id"] or {
                    cell["variant"] for cell in pair
                } != {"vulnerable", "patched"}:
                    raise ValueError("frozen continuation pair differs")
                endpoint = endpoint_receipt()
                endpoint_hash = create_json(
                    continuation_root / f"endpoint-preflight-{pair_index}.json", endpoint
                )
                pair_runs: list[str] = []
                for cell in pair:
                    order = int(cell["order"])
                    if order != FIRST_PAID_ORDER + len(later):
                        raise ValueError("continuation is not in frozen order")
                    stopped_records(manifest, private)
                    scoped_approval(args.continuation_approval, manifest)
                    if sha(args.original_approval.read_bytes()) != ORIGINAL_APPROVAL_SHA256:
                        raise ValueError("original paid approval bytes changed")
                    actual, active = await inventory(engine)
                    if actual != referenced or active:
                        raise ValueError("authoritative run inventory or reservation differs")
                    require_paid_approval(
                        original_approval,
                        manifest_raw,
                        cell_id=cell["cell_id"],
                        started_cell_ids={row["cell_id"] for row in old_records + later},
                        active_reservations=active,
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
                    if actual != referenced or active:
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
            combined = combined_journal(stopped_journal.read_bytes(), journal_path.read_bytes())
            result = await postcheck(
                manifest,
                combined,
                MANIFEST_SHA256,
                pairs_dir,
                state_root,
                store,
                engine,
                protocol_root=protocol_root,
            )
            duplicate = await postcheck(
                manifest,
                combined,
                MANIFEST_SHA256,
                pairs_dir,
                state_root,
                store,
                engine,
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
        "original-approval",
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
