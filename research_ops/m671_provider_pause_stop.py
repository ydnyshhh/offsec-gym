"""Read-only admission for the exact M6.7.1 provider-health stop after cell 86.

The original journal, log, pair receipts, and source traces are immutable. This
module never creates a cell outcome, releases a reservation, or calls a model.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m671_collect import CELL_CEILING, MANIFEST_SHA256, sha
from sqlalchemy import func, select, text

from offsecgym.research.m671_postcheck import _cell, journal_prefix, verify_manifest
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import action_reservations, events, model_reservations, runs

STOP_JOURNAL_SHA256 = "bbbb3288994825bba77300b78a6ea62ab6958a5203caaec15b3da8f86116ca2a"
STOP_LOG_SHA256 = "f21f4df12c5e293f2f0ab12c5e3319c69337613bc01fd6668d3d4c98c224acec"
STOP_PAIRS_SHA256 = "de94dc65c1fa4ed80130987d27eeb2e4a58e8b314975dcc1bc24f4efee03514b"
STOP_PREFLIGHTS_SHA256 = "1cddf20b8f21196bd9ac001dcb9588b57e47ac2e8730602b9b3c0034dd54daef"
STOP_RUNS = 172
STOP_EVENTS = 143_873
STOP_COMPLETED = 86
STOP_PAIRS = 42
STOP_RECORDED_COST = Decimal("35.802870")
STOP_CONSERVATIVE_COST = Decimal("39.402870")


def file_set_digest(files: list[Path]) -> str:
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise ValueError("stopped evidence contains an unsafe file")
    entries = [(path.name, sha(path.read_bytes())) for path in files]
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


def stopped_records(manifest: dict[str, object], private: Path) -> list[dict[str, object]]:
    journal = private / "journal.jsonl"
    log = private / "collector.log"
    if any(path.is_symlink() or not path.is_file() for path in (journal, log)):
        raise ValueError("stopped journal or log is unsafe")
    if sha(journal.read_bytes()) != STOP_JOURNAL_SHA256 or sha(log.read_bytes()) != STOP_LOG_SHA256:
        raise ValueError("stopped journal or log changed")
    pairs = sorted((private / "pairs").glob("pair-postcheck-*.json"))
    preflights = sorted(private.glob("endpoint-preflight-*.json"))
    if (
        len(pairs) != STOP_PAIRS
        or len(preflights) != STOP_PAIRS + 1
        or file_set_digest(pairs) != STOP_PAIRS_SHA256
        or file_set_digest(preflights) != STOP_PREFLIGHTS_SHA256
    ):
        raise ValueError("stopped pair or endpoint evidence changed")
    records = journal_prefix(manifest, journal.read_bytes(), manifest_sha256=MANIFEST_SHA256)
    if len(records) != STOP_COMPLETED or any(row["type"] != "cell_completed" for row in records):
        raise ValueError("stopped journal is not exactly 86 completed frozen cells")
    if [row["order"] for row in records] != list(range(1, STOP_COMPLETED + 1)):
        raise ValueError("stopped completion order differs")
    if any(
        row["terminal_status"] != "provider_failed"
        or row.get("failure_reason") != "provider_unavailable"
        or row.get("estimated_cost_usd") is not None
        for row in records[-2:]
    ):
        raise ValueError("retained cells 85 and 86 differ from provider-health stop")
    for pair_index in range(1, STOP_PAIRS + 1):
        receipt = json.loads((private / "pairs" / f"pair-postcheck-{pair_index}.json").read_bytes())
        cells = manifest["cells"][2 * pair_index - 2 : 2 * pair_index]
        source = records[2 * pair_index - 2 : 2 * pair_index]
        if (
            receipt.get("manifest_sha256") != MANIFEST_SHA256
            or receipt.get("pair_id") != cells[0]["pair_id"]
            or receipt.get("cell_ids") != [cell["cell_id"] for cell in cells]
            or receipt.get("run_ids") != [row["run_id"] for row in source]
            or receipt.get("endpoint_preflight_sha256")
            != sha((private / f"endpoint-preflight-{pair_index}.json").read_bytes())
        ):
            raise ValueError("stopped pair receipt differs from source prefix")
    return records


async def admit_stopped_prefix(
    manifest: dict[str, object], private: Path, state_root: Path, engine: object
) -> tuple[list[dict[str, object]], set[UUID]]:
    """Replay every stopped cell and the complete dedicated database read-only."""
    verify_manifest(manifest)
    records = stopped_records(manifest, private)
    store = PostgresEventStore(engine)
    referenced: set[UUID] = set()
    score_valid = 0
    recorded = Decimal(0)
    conservative = Decimal(0)
    for index, record in enumerate(records):
        row, run_ids = await _cell(
            manifest["cells"][index], record, events=store, state_root=state_root, manifest=manifest
        )
        if not row["trace_auditable"] or row["terminal_status"] != record["terminal_status"]:
            raise ValueError("stopped cell trace or terminal status is unauditable")
        referenced.update(run_ids)
        score_valid += row["score_valid"] is True
        if record["estimated_cost_usd"] is None:
            conservative += CELL_CEILING
        else:
            value = Decimal(record["estimated_cost_usd"])
            recorded += value
            conservative += value
    if (
        score_valid != 84
        or recorded != STOP_RECORDED_COST
        or conservative != STOP_CONSERVATIVE_COST
    ):
        raise ValueError("stopped score validity or cumulative cost differs")
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        actual = set((await connection.execute(select(runs.c.run_id))).scalars().all())
        event_count = (
            await connection.execute(select(func.count()).select_from(events))
        ).scalar_one()
        active_actions = (
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
        active_models = (
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
        await connection.rollback()
    if (
        actual != referenced
        or len(actual) != STOP_RUNS
        or event_count != STOP_EVENTS
        or active_actions
        or active_models
    ):
        raise ValueError("authoritative stopped PostgreSQL prefix or reservations differ")
    return records, referenced
