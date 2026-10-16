"""Read-only final replay with cell 109 retained as explicit missingness.

Uses the frozen M6.7.1 trace replay and factorial analysis unchanged. The
sole known model reservation for cell 109 is admitted as immutable evidence;
this module never settles it or synthesizes a source outcome.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m671_cell109_interrupted_stop import (
    INTERRUPTED_RUN_ID,
    INTERRUPTED_SETUP_RUN_ID,
    pinned_interrupted_trace,
    stopped_continuation_records,
    validate_known_reservation,
)
from m671_collect import CELL_CEILING, MANIFEST_SHA256, sha
from sqlalchemy import select, text

from offsecgym.research.m671_factorial_analysis import analyze_factorial
from offsecgym.research.m671_postcheck import _cell, _missing, verify_manifest
from offsecgym.research.m671_protocol import CELL_COUNT, PROTOCOL
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import action_reservations, model_reservations, run_usage, runs

FIRST_NEW_ORDER = 110
LAST_NEW_ORDER = 128


def new_records(
    raw: bytes, manifest: dict[str, object], *, header: dict[str, object]
) -> list[dict[str, object]]:
    lines = [json.loads(line) for line in raw.splitlines()]
    if (
        not lines
        or lines[0] != header
        or len(lines) != 1 + 2 * (LAST_NEW_ORDER - FIRST_NEW_ORDER + 1)
    ):
        raise ValueError("new continuation journal header or length differs")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, order in enumerate(range(FIRST_NEW_ORDER, LAST_NEW_ORDER + 1)):
        start, done = lines[1 + 2 * index : 3 + 2 * index]
        cell = manifest["cells"][order - 1]
        if (
            start.get("type") != "cell_started"
            or done.get("type") != "cell_completed"
            or start.get("order") != order
            or done.get("order") != order
            or start.get("cell_id") != cell["cell_id"]
            or done.get("cell_id") != cell["cell_id"]
            or start.get("run_id") != done.get("run_id")
            or start.get("run_id") in seen
            or done.get("experiment_sha256") != cell["experiment_sha256"]
        ):
            raise ValueError("new journal is not a unique frozen-order continuation")
        seen.add(start["run_id"])
        records.append(done)
    return records


async def postcheck(
    manifest: dict[str, object],
    private: Path,
    new_root: Path,
    state_root: Path,
    engine: object,
    *,
    header: dict[str, object],
    protocol_root: Path,
) -> dict[str, object]:
    """Replay every completed cell and admit exactly one known active reservation."""
    verify_manifest(manifest, root=protocol_root)
    old = stopped_continuation_records(manifest, private)
    later = new_records((new_root / "journal.jsonl").read_bytes(), manifest, header=header)
    store = PostgresEventStore(engine)
    await pinned_interrupted_trace(store, state_root)
    rows: list[dict[str, object]] = []
    referenced: set[UUID] = {INTERRUPTED_RUN_ID, INTERRUPTED_SETUP_RUN_ID}
    for index, cell in enumerate(manifest["cells"]):
        order = index + 1
        if order == 109:
            rows.append(_missing(cell, "started without trustworthy completion", started=True))
            continue
        record = old[index] if order < 109 else later[order - FIRST_NEW_ORDER]
        row, run_ids = await _cell(
            cell, record, events=store, state_root=state_root, manifest=manifest
        )
        if not row["trace_auditable"] or row["terminal_status"] != record["terminal_status"]:
            raise ValueError("completed source trace is unauditable")
        rows.append(row)
        referenced.update(run_ids)
    if len(rows) != CELL_COUNT:
        raise ValueError("assigned row count differs")
    if len(list((new_root / "pairs").glob("pair-postcheck-*.json"))) != CELL_COUNT // 2:
        raise ValueError("pair receipt inventory differs")
    for pair_index in range(1, CELL_COUNT // 2 + 1):
        cells = manifest["cells"][2 * pair_index - 2 : 2 * pair_index]
        path = new_root / "pairs" / f"pair-postcheck-{pair_index}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("pair receipt absent or unsafe")
        receipt = json.loads(path.read_bytes())
        run_ids = [rows[cell["order"] - 1].get("run_id") for cell in cells]
        if (
            receipt.get("manifest_sha256") != MANIFEST_SHA256
            or receipt.get("pair_id") != cells[0]["pair_id"]
            or receipt.get("cell_ids") != [cell["cell_id"] for cell in cells]
            or receipt.get("run_ids") != run_ids
        ):
            raise ValueError("pair receipt differs from frozen assignment")
        if pair_index >= 55:
            preflight = new_root / f"endpoint-preflight-{pair_index}.json"
            if (
                preflight.is_symlink()
                or not preflight.is_file()
                or receipt.get("endpoint_preflight_sha256") != sha(preflight.read_bytes())
            ):
                raise ValueError("new pair endpoint preflight differs")
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        actual = set((await connection.execute(select(runs.c.run_id))).scalars().all())
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
    validate_known_reservation(active_models, usage, other_reserved_usage)
    if actual != referenced or actions:
        raise ValueError("source run inventory or known active reservation differs")
    known = sum(
        (
            Decimal(str(row["estimated_cost_usd"]))
            for row in rows
            if row["estimated_cost_usd"] is not None
        ),
        Decimal(0),
    )
    conservative = known + CELL_CEILING
    if conservative > Decimal(manifest["cumulative_estimated_cost_stop_usd"]):
        raise ValueError("frozen cumulative estimated cost cap exceeded")
    analysis = analyze_factorial(
        rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
    )
    raw = (
        json.dumps({"rows": rows, "analysis": analysis}, sort_keys=True, indent=2).encode() + b"\n"
    )
    duplicate = (
        json.dumps(
            {
                "rows": rows,
                "analysis": analyze_factorial(
                    rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
                ),
            },
            sort_keys=True,
            indent=2,
        ).encode()
        + b"\n"
    )
    if raw != duplicate:
        raise ValueError("frozen factorial analysis is nondeterministic")
    return {
        "protocol": PROTOCOL,
        "assigned": CELL_COUNT,
        "started": CELL_COUNT,
        "trace_auditable": sum(row["trace_auditable"] is True for row in rows),
        "score_valid": sum(row["score_valid"] is True for row in rows),
        "resource_auditable": sum(row["resource_auditable"] is True for row in rows),
        "recorded_estimated_cost_usd": str(known),
        "conservative_estimated_cost_usd": str(conservative),
        "analysis_sha256": sha(raw),
        "analysis_bytes": raw,
    }
