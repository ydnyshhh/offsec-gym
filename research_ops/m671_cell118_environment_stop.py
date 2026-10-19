"""Read-only admission of the exact stopped M6.7.1 cell-118 failure."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m671_cell109_interrupted_stop import (
    INTERRUPTED_RUN_ID,
    INTERRUPTED_SETUP_RUN_ID,
    pinned_interrupted_trace,
    stopped_continuation_records,
)
from m671_cell113_environment_stop import (
    FAILED_RUN_ID as CELL113_RUN_ID,
)
from m671_cell113_environment_stop import (
    pinned_failed_trace as pinned_cell113_trace,
)
from m671_cell113_environment_stop import (
    stopped_cell113_records,
)
from m671_cell116_environment_stop import (
    FAILED_RUN_ID as CELL116_RUN_ID,
)
from m671_cell116_environment_stop import (
    pinned_failed_trace as pinned_cell116_trace,
)
from m671_cell116_environment_stop import (
    stopped_cell116_records,
)
from m671_collect import CELL_CEILING, MANIFEST_SHA256, sha
from m671_resume_after_cell116_environment import (
    journal_header as previous_header,
)
from m671_resume_after_cell116_environment import (
    known_reservation,
)
from sqlalchemy import func, select, text

from offsecgym.research.m671_postcheck import _cell, trace_digest
from offsecgym.schemas.events import ModelCallStarted, RunCompleted, RunStarted
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import action_reservations, events, runs

STOPPED_JOURNAL_SHA256 = "5a9952abf49f9ab21bbe39d9628de17da0c691c6632a0bca6f5242a30594c032"
STOPPED_LOG_SHA256 = "695fcd254d9ee74308a48e745f320def11e20f50420094502300491fc3221e9c"
STOPPED_FILE_SET_SHA256 = "f75533a8bb8d1fe477b4bdf0c42bb388658909fab01fe42ea1942fee9ce7006f"
FAILED_RECEIPT_SHA256 = "240da4700057ea328004cef3dbe6a9a3f54c633d5143d9391d1f22ea296f87e7"
FAILED_RUN_ID = UUID("353e42d3-aff2-4d67-b554-ef8ef2f692d8")
FAILED_TRACE_SHA256 = "30218beba258a5b61841e13414c77394e94c567a0ebc085f39353d251ddc8066"
PREFIX_RUNS = 233
PREFIX_EVENTS = 189_225
PREFIX_PAIRS = 58
COMPLETED_ORDER = 117
FAILED_ORDER = 118


def file_set_digest(root: Path) -> str:
    paths = sorted(path for path in root.rglob("*") if path.is_file() or path.is_symlink())
    if len(paths) != 63 or any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("cell 118 stopped file inventory differs")
    entries = [(path.relative_to(root).as_posix(), sha(path.read_bytes())) for path in paths]
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


def stopped_cell118_records(
    manifest: dict[str, object], private: Path
) -> tuple[dict[str, object], dict[str, object]]:
    root = private / "cell116-continuation"
    journal = root / "journal.jsonl"
    log = private / "cell116-continuation.log"
    failure_path = root / "failed-cell-118.json"
    preflight = root / "endpoint-preflight-59.json"
    if (
        root.is_symlink()
        or not root.is_dir()
        or any(
            path.is_symlink() or not path.is_file()
            for path in (journal, log, failure_path, preflight)
        )
        or sha(journal.read_bytes()) != STOPPED_JOURNAL_SHA256
        or sha(log.read_bytes()) != STOPPED_LOG_SHA256
        or file_set_digest(root) != STOPPED_FILE_SET_SHA256
        or sha(failure_path.read_bytes()) != FAILED_RECEIPT_SHA256
    ):
        raise ValueError("cell 118 stopped evidence differs")
    lines = [json.loads(line) for line in journal.read_bytes().splitlines()]
    if len(lines) != 4 or any(lines[0].get(k) != v for k, v in previous_header().items()):
        raise ValueError("cell 118 stopped journal header or length differs")
    start, completed, failed = lines[1:]
    cell117, cell118 = manifest["cells"][116:118]
    receipt = json.loads(failure_path.read_bytes())
    if (
        start.get("type") != "cell_started"
        or completed.get("type") != "cell_completed"
        or start.get("order") != COMPLETED_ORDER
        or completed.get("order") != COMPLETED_ORDER
        or start.get("cell_id") != cell117["cell_id"]
        or completed.get("cell_id") != cell117["cell_id"]
        or start.get("run_id") != completed.get("run_id")
        or completed.get("experiment_sha256") != cell117["experiment_sha256"]
        or completed.get("terminal_status") != "provider_failed"
        or failed.get("type") != "cell_started"
        or failed.get("order") != FAILED_ORDER
        or failed.get("cell_id") != cell118["cell_id"]
        or failed.get("run_id") != str(FAILED_RUN_ID)
        or failed.get("run_id") == start.get("run_id")
        or receipt
        != {
            "failure_class": "DockerCommandError",
            "model_call_starts": 0,
            "order": FAILED_ORDER,
            "run_id": str(FAILED_RUN_ID),
            "terminal_status": "environment_failed",
            "trace_sha256": FAILED_TRACE_SHA256,
        }
    ):
        raise ValueError("cell 118 failed start or receipt differs")
    for pair_index in range(1, PREFIX_PAIRS + 1):
        path = root / "pairs" / f"pair-postcheck-{pair_index}.json"
        pair_receipt = json.loads(path.read_bytes())
        cells = manifest["cells"][2 * pair_index - 2 : 2 * pair_index]
        if (
            pair_receipt.get("manifest_sha256") != MANIFEST_SHA256
            or pair_receipt.get("pair_id") != cells[0]["pair_id"]
            or pair_receipt.get("cell_ids") != [item["cell_id"] for item in cells]
        ):
            raise ValueError("retained pair receipt differs")
    return completed, failed


async def pinned_failed_trace(store: PostgresEventStore) -> None:
    trace = await store.read_run(FAILED_RUN_ID)
    if (
        len(trace) != 2
        or trace_digest(trace) != FAILED_TRACE_SHA256
        or not isinstance(trace[0], RunStarted)
        or not isinstance(trace[1], RunCompleted)
        or trace[1].status != "environment_failed"
        or [event.sequence_number for event in trace] != [1, 2]
        or any(isinstance(event, ModelCallStarted) for event in trace)
    ):
        raise ValueError("cell 118 source setup failure drifted")


async def admit_cell118_stop(
    manifest: dict[str, object], private: Path, state_root: Path, engine: object
) -> tuple[dict[str, object], dict[str, object], set[UUID], Decimal]:
    """Replay all 114 completed cells and the four exact missing cells."""
    old = stopped_continuation_records(manifest, private)
    middle, _ = stopped_cell113_records(manifest, private)
    later, _ = stopped_cell116_records(manifest, private)
    completed, failed = stopped_cell118_records(manifest, private)
    store = PostgresEventStore(engine)
    await pinned_interrupted_trace(store, state_root)
    await pinned_cell113_trace(store, state_root)
    await pinned_cell116_trace(store)
    await pinned_failed_trace(store)
    referenced: set[UUID] = {
        INTERRUPTED_RUN_ID,
        INTERRUPTED_SETUP_RUN_ID,
        CELL113_RUN_ID,
        CELL116_RUN_ID,
        FAILED_RUN_ID,
    }
    conservative = 4 * CELL_CEILING
    for order, record in (
        *zip(range(1, 109), old[:-1], strict=True),
        *zip(range(110, 113), middle, strict=True),
        *zip(range(114, 116), later, strict=True),
        (COMPLETED_ORDER, completed),
    ):
        row, source_ids = await _cell(
            manifest["cells"][order - 1],
            record,
            events=store,
            state_root=state_root,
            manifest=manifest,
        )
        if not row["trace_auditable"] or row["terminal_status"] != record["terminal_status"]:
            raise ValueError("completed source prefix is unauditable")
        referenced.update(source_ids)
        conservative += (
            Decimal(str(record["estimated_cost_usd"]))
            if record["estimated_cost_usd"] is not None
            else CELL_CEILING
        )
    await known_reservation(engine)
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        actual = set((await connection.execute(select(runs.c.run_id))).scalars().all())
        event_count = (
            await connection.execute(select(func.count()).select_from(events))
        ).scalar_one()
        active_actions = (
            await connection.execute(
                select(func.count())
                .select_from(action_reservations)
                .where(action_reservations.c.released_at.is_(None))
            )
        ).scalar_one()
        await connection.rollback()
    if (
        len(old) != 109
        or len(middle) != 3
        or len(later) != 2
        or actual != referenced
        or len(actual) != PREFIX_RUNS
        or event_count != PREFIX_EVENTS
        or active_actions != 0
        or conservative != Decimal("61.469619")
    ):
        raise ValueError("cell 118 authoritative stop differs")
    return completed, failed, referenced, conservative
