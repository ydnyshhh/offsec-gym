"""Read-only admission of the exact cell-116 environment failure.

Cells 109, 113, and 116 remain missing. No started cell is retried, and the
cell-109 reservation is never settled by this module.
"""

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
from m671_collect import CELL_CEILING, MANIFEST_SHA256, sha
from m671_resume_after_cell113_environment import journal_header as previous_header
from m671_resume_after_cell113_environment import known_reservation
from sqlalchemy import func, select, text

from offsecgym.research.m671_postcheck import _cell, trace_digest
from offsecgym.schemas.events import ModelCallStarted, RunCompleted, RunStarted
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import action_reservations, events, runs

STOPPED_JOURNAL_SHA256 = "2567df59e5538b80015b2bf4e6af66e72980a87b9d92970775788f8a6e54d875"
STOPPED_LOG_SHA256 = "fc050dd8ced0aa2d3a1b92fb2481b942a29eb25bac099e8bf5306eb05ac42f2c"
STOPPED_FILE_SET_SHA256 = "52153809c00e521d7c269015849f3c9e869e1dba28df41110a8656c00f91df8b"
FAILED_RECEIPT_SHA256 = "ce80b386eeeaa44241ee80e3e1dadb2e753276296f7977a3c583baba0652ee99"
FAILED_RUN_ID = UUID("51583f03-d229-453a-ab45-e921592ffb6d")
FAILED_TRACE_SHA256 = "623d14c618971afd99fc16bd3b47dc024eec4efd86c4bf8b5497917c5ebadbaa"
PREFIX_RUNS = 230
PREFIX_EVENTS = 188_594
PREFIX_PAIRS = 57
FIRST_COMPLETED = 114
LAST_COMPLETED = 115
FAILED_ORDER = 116


def file_set_digest(root: Path) -> str:
    paths = sorted(path for path in root.rglob("*") if path.is_file() or path.is_symlink())
    if len(paths) != 62 or any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("cell 116 stopped file inventory differs")
    entries = [(path.relative_to(root).as_posix(), sha(path.read_bytes())) for path in paths]
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


def stopped_cell116_records(
    manifest: dict[str, object], private: Path
) -> tuple[list[dict[str, object]], dict[str, object]]:
    root = private / "cell113-continuation"
    journal = root / "journal.jsonl"
    log = private / "cell113-continuation.log"
    failure_path = root / "failed-cell-116.json"
    if (
        root.is_symlink()
        or journal.is_symlink()
        or log.is_symlink()
        or failure_path.is_symlink()
        or sha(journal.read_bytes()) != STOPPED_JOURNAL_SHA256
        or sha(log.read_bytes()) != STOPPED_LOG_SHA256
        or file_set_digest(root) != STOPPED_FILE_SET_SHA256
        or sha(failure_path.read_bytes()) != FAILED_RECEIPT_SHA256
    ):
        raise ValueError("cell 116 stopped evidence differs")
    lines = [json.loads(line) for line in journal.read_bytes().splitlines()]
    if len(lines) != 6 or any(lines[0].get(k) != v for k, v in previous_header().items()):
        raise ValueError("cell 116 stopped journal header or length differs")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, order in enumerate(range(FIRST_COMPLETED, LAST_COMPLETED + 1)):
        started, completed = lines[1 + index * 2 : 3 + index * 2]
        cell = manifest["cells"][order - 1]
        if (
            started.get("type") != "cell_started"
            or completed.get("type") != "cell_completed"
            or started.get("order") != order
            or completed.get("order") != order
            or started.get("cell_id") != cell["cell_id"]
            or completed.get("cell_id") != cell["cell_id"]
            or started.get("run_id") != completed.get("run_id")
            or started.get("run_id") in seen
            or completed.get("experiment_sha256") != cell["experiment_sha256"]
            or completed.get("terminal_status")
            != ("provider_failed" if order == 114 else "budget_exhausted")
        ):
            raise ValueError("cell 116 stopped prefix is not frozen order")
        seen.add(started["run_id"])
        records.append(completed)
    failed = lines[-1]
    cell = manifest["cells"][FAILED_ORDER - 1]
    receipt = json.loads(failure_path.read_bytes())
    if (
        failed.get("type") != "cell_started"
        or failed.get("order") != FAILED_ORDER
        or failed.get("cell_id") != cell["cell_id"]
        or failed.get("run_id") != str(FAILED_RUN_ID)
        or failed.get("run_id") in seen
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
        raise ValueError("cell 116 failed start or receipt differs")
    for pair_index in range(1, PREFIX_PAIRS + 1):
        receipt_path = root / "pairs" / f"pair-postcheck-{pair_index}.json"
        pair_receipt = json.loads(receipt_path.read_bytes())
        cells = manifest["cells"][2 * pair_index - 2 : 2 * pair_index]
        if (
            pair_receipt.get("manifest_sha256") != MANIFEST_SHA256
            or pair_receipt.get("pair_id") != cells[0]["pair_id"]
            or pair_receipt.get("cell_ids") != [item["cell_id"] for item in cells]
        ):
            raise ValueError("retained pair receipt differs")
    return records, failed


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
        raise ValueError("cell 116 source setup failure drifted")


async def admit_cell116_stop(
    manifest: dict[str, object], private: Path, state_root: Path, engine: object
) -> tuple[list[dict[str, object]], set[UUID], Decimal]:
    """Replay the exact 113-complete prefix and three retained missing cells."""
    old = stopped_continuation_records(manifest, private)
    middle, _cell113 = stopped_cell113_records(manifest, private)
    later, _cell116 = stopped_cell116_records(manifest, private)
    store = PostgresEventStore(engine)
    await pinned_interrupted_trace(store, state_root)
    await pinned_cell113_trace(store, state_root)
    await pinned_failed_trace(store)
    referenced: set[UUID] = {
        INTERRUPTED_RUN_ID,
        INTERRUPTED_SETUP_RUN_ID,
        CELL113_RUN_ID,
        FAILED_RUN_ID,
    }
    conservative = 3 * CELL_CEILING
    for order, record in (
        *zip(range(1, 109), old[:-1], strict=True),
        *zip(range(110, 113), middle, strict=True),
        *zip(range(FIRST_COMPLETED, LAST_COMPLETED + 1), later, strict=True),
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
        or conservative != Decimal("57.869619")
    ):
        raise ValueError("cell 116 authoritative stop differs")
    return later, referenced, conservative
