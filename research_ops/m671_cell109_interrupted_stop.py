"""Read-only admission of the exact M6.7.1 interruption during cell 109.

The pending provider outcome is unknown. This module never settles its model
reservation, creates a terminal event, or authorizes another paid call.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m671_collect import CELL_CEILING, MANIFEST_SHA256, sha
from m671_provider_pause_stop import stopped_records
from m671_resume_after_precell_failure import admit_failed_launch, combined_journal
from sqlalchemy import func, select, text

from offsecgym.research.m671_postcheck import _cell, journal_prefix, trace_digest, verify_manifest
from offsecgym.schemas.events import ModelCallCompleted, ModelCallStarted, RunCompleted, RunStarted
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import (
    action_reservations,
    events,
    model_reservations,
    run_usage,
    runs,
)

CONTINUATION_JOURNAL_SHA256 = "eb70456653062f0661325d54df3276b961c6ead87724ec0d734a9e2eb52c8789"
CONTINUATION_LOG_SHA256 = "f5d791f32f8d4f7a16e852283b4bb572397e34b9cffc899e8b9a01e8f626c084"
CONTINUATION_FILE_SET_SHA256 = "93de9939bb80b6997540a9ce18febce86ca13b0a804e1c45d6e7822cf54bea5f"
INTERRUPTED_RUN_ID = UUID("35e3ab88-6e00-4a38-9126-2feb4eced9a3")
INTERRUPTED_SETUP_RUN_ID = UUID("9cc36809-54d2-4a5a-a638-6b8eff3cfd25")
INTERRUPTED_TRACE_SHA256 = "a14d26651459e2e5adef8a083756fbbd792517424513440555ba21c5a867cd55"
INTERRUPTED_EVENT_IDENTITY_SHA256 = (
    "d5a40adf913d3c19598d67e89d6c7f6de3020bb04d51c6b68e9edb306e87beec"
)
INTERRUPTED_RECEIPT_SHA256 = "9d8821c31835a14919128e5ca68c1928d683d308d2268cd6789d7932deb4e862"
UNSETTLED_CALL_ID = UUID("088fc779-4acc-4cec-9e50-c6d52f4af183")
UNSETTLED_RESERVED_TOKENS = 20_523
UNSETTLED_RESERVED_COST_MICROUSD = 159_873
RECORDED_USED_COST_MICROUSD = 234_096
PREFIX_COMPLETED = 108
PREFIX_RUNS = 218
PREFIX_EVENTS = 181_603
PREFIX_PAIRS = 54


def _file_set_digest(root: Path) -> str:
    paths = sorted(path for path in root.rglob("*") if path.is_file() or path.is_symlink())
    if len(paths) != 68 or any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("interrupted continuation file inventory differs")
    entries = [(path.relative_to(root).as_posix(), sha(path.read_bytes())) for path in paths]
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


def stopped_continuation_records(
    manifest: dict[str, object], private: Path
) -> list[dict[str, object]]:
    """Pin both prior launches and return the exact 108-complete/109-started prefix."""
    verify_manifest(manifest)
    original = stopped_records(manifest, private)
    admit_failed_launch(private)
    root = private / "precell-order-fix-continuation"
    journal = root / "journal.jsonl"
    log = private / "precell-order-fix-continuation.log"
    if (
        root.is_symlink()
        or journal.is_symlink()
        or log.is_symlink()
        or not journal.is_file()
        or not log.is_file()
        or sha(journal.read_bytes()) != CONTINUATION_JOURNAL_SHA256
        or sha(log.read_bytes()) != CONTINUATION_LOG_SHA256
        or _file_set_digest(root) != CONTINUATION_FILE_SET_SHA256
    ):
        raise ValueError("interrupted continuation evidence differs")
    combined = combined_journal((private / "journal.jsonl").read_bytes(), journal.read_bytes())
    records = journal_prefix(manifest, combined, manifest_sha256=MANIFEST_SHA256)
    if (
        len(records) != PREFIX_COMPLETED + 1
        or records[: len(original)] != original
        or [row["order"] for row in records] != list(range(1, PREFIX_COMPLETED + 2))
        or any(row["type"] != "cell_completed" for row in records[:-1])
        or records[-1]["type"] != "interrupted"
        or UUID(records[-1]["run_id"]) != INTERRUPTED_RUN_ID
    ):
        raise ValueError("cell 109 is not the sole interrupted frozen-order tail")
    for pair_index in range(1, PREFIX_PAIRS + 1):
        path = root / "pairs" / f"pair-postcheck-{pair_index}.json"
        receipt = json.loads(path.read_bytes())
        cells = manifest["cells"][2 * pair_index - 2 : 2 * pair_index]
        source = records[2 * pair_index - 2 : 2 * pair_index]
        if (
            receipt.get("manifest_sha256") != MANIFEST_SHA256
            or receipt.get("pair_id") != cells[0]["pair_id"]
            or receipt.get("cell_ids") != [cell["cell_id"] for cell in cells]
            or receipt.get("run_ids") != [row["run_id"] for row in source]
        ):
            raise ValueError("interrupted continuation pair receipt differs")
    return records


async def pinned_interrupted_trace(store: PostgresEventStore, state_root: Path) -> None:
    """Require the exact partial trace and checkpoint before another cell."""
    trace = await store.read_run(INTERRUPTED_RUN_ID)
    receipt = state_root / "critical_checkpoints" / f"{INTERRUPTED_RUN_ID.hex}.json"
    if (
        len(trace) != 1296
        or trace_digest(trace) != INTERRUPTED_TRACE_SHA256
        or sha(
            json.dumps(
                [(event.sequence_number, str(event.event_id), event.type) for event in trace],
                separators=(",", ":"),
            ).encode()
        )
        != INTERRUPTED_EVENT_IDENTITY_SHA256
        or any(isinstance(event, RunCompleted) for event in trace)
        or not isinstance(trace[-1], ModelCallStarted)
        or trace[-1].call_id != UNSETTLED_CALL_ID
        or receipt.is_symlink()
        or not receipt.is_file()
        or sha(receipt.read_bytes()) != INTERRUPTED_RECEIPT_SHA256
    ):
        raise ValueError("retained interrupted trace or checkpoint drifted")


def validate_known_reservation(
    active: list[Mapping[str, object]], usage: Mapping[str, object], other_reserved_usage: object
) -> None:
    """Require exactly the retained call, with no other reserved budget."""
    if (
        len(active) != 1
        or active[0]["run_id"] != INTERRUPTED_RUN_ID
        or active[0]["call_id"] != UNSETTLED_CALL_ID
        or active[0]["worker_id"] is not None
        or active[0]["reserved_tokens"] != UNSETTLED_RESERVED_TOKENS
        or active[0]["reserved_cost_microusd"] != UNSETTLED_RESERVED_COST_MICROUSD
        or usage["used_cost_microusd"] != RECORDED_USED_COST_MICROUSD
        or usage["reserved_tokens"] != UNSETTLED_RESERVED_TOKENS
        or usage["reserved_cost_microusd"] != UNSETTLED_RESERVED_COST_MICROUSD
        or other_reserved_usage
    ):
        raise ValueError("known interrupted reservation drifted")


async def admit_interrupted_stop(
    manifest: dict[str, object], private: Path, state_root: Path, engine: object
) -> tuple[list[dict[str, object]], set[UUID], Decimal]:
    """Replay all completed traces and verify the sole known unsettled call."""
    records = stopped_continuation_records(manifest, private)
    store = PostgresEventStore(engine)
    referenced: set[UUID] = set()
    conservative = Decimal(0)
    for cell, record in zip(manifest["cells"][:PREFIX_COMPLETED], records[:-1], strict=True):
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
    trace = await store.read_run(INTERRUPTED_RUN_ID)
    setup_trace = await store.read_run(INTERRUPTED_SETUP_RUN_ID)
    receipt = state_root / "critical_checkpoints" / f"{INTERRUPTED_RUN_ID.hex}.json"
    receipt_data = json.loads(receipt.read_bytes())
    identity = sha(
        json.dumps(
            [(event.sequence_number, str(event.event_id), event.type) for event in trace],
            separators=(",", ":"),
        ).encode()
    )
    starts = [event for event in trace if isinstance(event, ModelCallStarted)]
    completions = [event for event in trace if isinstance(event, ModelCallCompleted)]
    if (
        len(trace) != 1296
        or trace_digest(trace) != INTERRUPTED_TRACE_SHA256
        or identity != INTERRUPTED_EVENT_IDENTITY_SHA256
        or [event.sequence_number for event in trace] != list(range(1, len(trace) + 1))
        or len({event.event_id for event in trace}) != len(trace)
        or not isinstance(trace[0], RunStarted)
        or any(isinstance(event, RunCompleted) for event in trace)
        or not isinstance(trace[-1], ModelCallStarted)
        or len(starts) != 7
        or len(completions) != 6
        or trace[-1].call_id != UNSETTLED_CALL_ID
        or len(setup_trace) == 0
        or sha(receipt.read_bytes()) != INTERRUPTED_RECEIPT_SHA256
        or UUID(receipt_data["setup_run_id"]) != INTERRUPTED_SETUP_RUN_ID
        or receipt_data["checkpoint_id"] != manifest["cells"][108]["checkpoint_id"]
    ):
        raise ValueError("interrupted source trace or checkpoint differs")
    referenced.update((INTERRUPTED_RUN_ID, INTERRUPTED_SETUP_RUN_ID))
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
        active_models = (
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
            await connection.execute(
                select(func.count())
                .select_from(run_usage)
                .where(
                    run_usage.c.run_id != INTERRUPTED_RUN_ID,
                    (run_usage.c.reserved_tokens != 0) | (run_usage.c.reserved_cost_microusd != 0),
                )
            )
        ).scalar_one()
        await connection.rollback()
    validate_known_reservation(active_models, usage, other_reserved_usage)
    if (
        actual != referenced
        or len(actual) != PREFIX_RUNS
        or event_count != PREFIX_EVENTS
        or active_actions != 0
    ):
        raise ValueError("dedicated PostgreSQL or known reservation drifted")
    return records, referenced, conservative + CELL_CEILING
