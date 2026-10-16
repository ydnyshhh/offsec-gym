"""Read-only admission of the exact M6.7.1 setup failure in cell 113.

The source reported environment_failed before any provider call. Cell 113 is
retained as explicit missingness; this module never retries it or mutates the
unsettled cell-109 reservation.
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
from m671_collect import CELL_CEILING, MANIFEST_SHA256, sha
from m671_resume_after_cell109_interruption import journal_header as previous_header
from m671_resume_after_cell109_interruption import known_reservation
from sqlalchemy import func, select, text

from offsecgym.research.m671_postcheck import _cell, trace_digest
from offsecgym.schemas.events import ModelCallStarted, RunCompleted, RunStarted
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import action_reservations, events, runs

STOPPED_JOURNAL_SHA256 = "37cf7d1e075a415bd0ce9dc46b4c3851cf5bf84e74f07980e5c3047821bd659f"
STOPPED_LOG_SHA256 = "4f48fcb53804834cfaa693e8d768ef37079386bcf2e13bda1176e3ae4d14e85d"
STOPPED_FILE_SET_SHA256 = "8b96a38a6cfbf327194a084502aecf5c90cdf73fae978d12b238d73171c02742"
FAILED_RUN_ID = UUID("bb85369e-e9fc-4f77-a431-eef6fe70a632")
FAILED_TRACE_SHA256 = "c7e18f112031cd170833c06052d90927b384b3d266ca5028fb30e5ae66f195f1"
FAILED_INSTANCE_ID = UUID("4bdcfd5c-bfb4-4f87-a2bb-7be175b15ec1")
FAILED_INSTANCE_SHA256 = "57892710a8f118a4da9ca70da3d817e5a786b3049a10fa036d3bac866a703060"
PREFIX_RUNS = 225
PREFIX_EVENTS = 186_537
PREFIX_PAIRS = 56
FIRST_COMPLETED = 110
LAST_COMPLETED = 112
FAILED_ORDER = 113


def file_set_digest(root: Path) -> str:
    paths = sorted(path for path in root.rglob("*") if path.is_file() or path.is_symlink())
    if len(paths) != 61 or any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("cell 113 stopped file inventory differs")
    entries = [(path.relative_to(root).as_posix(), sha(path.read_bytes())) for path in paths]
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


def stopped_cell113_records(
    manifest: dict[str, object], private: Path
) -> tuple[list[dict[str, object]], dict[str, object]]:
    root = private / "cell109-continuation"
    journal = root / "journal.jsonl"
    log = private / "cell109-continuation.log"
    if (
        root.is_symlink()
        or journal.is_symlink()
        or log.is_symlink()
        or sha(journal.read_bytes()) != STOPPED_JOURNAL_SHA256
        or sha(log.read_bytes()) != STOPPED_LOG_SHA256
        or file_set_digest(root) != STOPPED_FILE_SET_SHA256
    ):
        raise ValueError("cell 113 stopped evidence differs")
    lines = [json.loads(line) for line in journal.read_bytes().splitlines()]
    expected_header = previous_header()
    if len(lines) != 8 or any(lines[0].get(k) != v for k, v in expected_header.items()):
        raise ValueError("cell 113 stopped journal header or length differs")
    records = []
    run_ids: set[str] = set()
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
            or started.get("run_id") in run_ids
            or completed.get("experiment_sha256") != cell["experiment_sha256"]
        ):
            raise ValueError("cell 113 stopped prefix is not frozen order")
        run_ids.add(started["run_id"])
        records.append(completed)
    failed = lines[-1]
    cell = manifest["cells"][FAILED_ORDER - 1]
    if (
        failed.get("type") != "cell_started"
        or failed.get("order") != FAILED_ORDER
        or failed.get("cell_id") != cell["cell_id"]
        or failed.get("run_id") != str(FAILED_RUN_ID)
        or failed.get("run_id") in run_ids
    ):
        raise ValueError("cell 113 failed start differs from frozen assignment")
    for pair_index in range(1, PREFIX_PAIRS + 1):
        receipt_path = root / "pairs" / f"pair-postcheck-{pair_index}.json"
        receipt = json.loads(receipt_path.read_bytes())
        cells = manifest["cells"][2 * pair_index - 2 : 2 * pair_index]
        if (
            receipt.get("manifest_sha256") != MANIFEST_SHA256
            or receipt.get("pair_id") != cells[0]["pair_id"]
            or receipt.get("cell_ids") != [cell["cell_id"] for cell in cells]
        ):
            raise ValueError("retained pair receipt differs")
    return records, failed


async def pinned_failed_trace(store: PostgresEventStore, state_root: Path) -> None:
    trace = await store.read_run(FAILED_RUN_ID)
    instance = state_root / "instances" / FAILED_INSTANCE_ID.hex / "manifest.json"
    if (
        len(trace) != 2
        or trace_digest(trace) != FAILED_TRACE_SHA256
        or not isinstance(trace[0], RunStarted)
        or not isinstance(trace[1], RunCompleted)
        or trace[1].status != "environment_failed"
        or [event.sequence_number for event in trace] != [1, 2]
        or any(isinstance(event, ModelCallStarted) for event in trace)
        or instance.is_symlink()
        or not instance.is_file()
        or sha(instance.read_bytes()) != FAILED_INSTANCE_SHA256
        or json.loads(instance.read_bytes()).get("state") != "destroyed"
    ):
        raise ValueError("cell 113 source setup failure drifted")


async def admit_cell113_stop(
    manifest: dict[str, object], private: Path, state_root: Path, engine: object
) -> tuple[list[dict[str, object]], set[UUID], Decimal]:
    """Replay the exact 111-complete prefix and both retained missing cells."""
    old = stopped_continuation_records(manifest, private)
    later, _failed = stopped_cell113_records(manifest, private)
    store = PostgresEventStore(engine)
    await pinned_interrupted_trace(store, state_root)
    await pinned_failed_trace(store, state_root)
    referenced: set[UUID] = {INTERRUPTED_RUN_ID, INTERRUPTED_SETUP_RUN_ID}
    conservative = CELL_CEILING
    for cell, record in zip(manifest["cells"][:108], old[:-1], strict=True):
        row, source_ids = await _cell(
            cell, record, events=store, state_root=state_root, manifest=manifest
        )
        if not row["trace_auditable"] or row["terminal_status"] != record["terminal_status"]:
            raise ValueError("completed source prefix is unauditable")
        referenced.update(source_ids)
        conservative += (
            Decimal(str(record["estimated_cost_usd"]))
            if record["estimated_cost_usd"] is not None
            else CELL_CEILING
        )
    for order, record in zip(range(FIRST_COMPLETED, LAST_COMPLETED + 1), later, strict=True):
        row, source_ids = await _cell(
            manifest["cells"][order - 1],
            record,
            events=store,
            state_root=state_root,
            manifest=manifest,
        )
        if not row["trace_auditable"] or row["terminal_status"] != record["terminal_status"]:
            raise ValueError("cell 110-112 source replay differs")
        referenced.update(source_ids)
        conservative += (
            Decimal(str(record["estimated_cost_usd"]))
            if record["estimated_cost_usd"] is not None
            else CELL_CEILING
        )
    referenced.add(FAILED_RUN_ID)
    conservative += CELL_CEILING
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
        or actual != referenced
        or len(actual) != PREFIX_RUNS
        or event_count != PREFIX_EVENTS
        or active_actions != 0
        or conservative != Decimal("53.883117")
    ):
        raise ValueError("cell 113 authoritative stop differs")
    return later, referenced, conservative
