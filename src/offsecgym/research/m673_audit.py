"""Read-only, hash-bound M6.7.3 source snapshot and model-artifact loader.

This module does not dispatch a tool, call a provider, or change a reservation.
Private model requests remain in memory and must not be serialized publicly.
"""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.providers.artifacts import ModelCallArtifacts
from offsecgym.research.m671_postcheck import _cell, _missing, trace_digest, verify_manifest
from offsecgym.schemas.events import (
    ModelCallCompleted,
    ModelCallStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import action_reservations, events, runs

MANIFEST_SHA = "d51ba9d9dffe12c6e809b32196f2c48937f72e33768ffb2ad050218d4be41e2f"
ASSIGNMENT_SHA = "f0ba253f2d162300b453d960f6a195505e9c04a9b0001f43f712f8be50b8540b"
SOURCE_COMMIT = "f5273e6d59d2810ac7de2646c238a2d0672dc042"
PROTOCOL_COMMIT = "86c5406024e961eaaeacb851d9bf51d4a550df04"
LAST_JOURNAL_SHA = "5a9952abf49f9ab21bbe39d9628de17da0c691c6632a0bca6f5242a30594c032"
LAST_LOG_SHA = "695fcd254d9ee74308a48e745f320def11e20f50420094502300491fc3221e9c"
LAST_FILE_SET_SHA = "f75533a8bb8d1fe477b4bdf0c42bb388658909fab01fe42ea1942fee9ce7006f"
LAST_FAILURE_SHA = "240da4700057ea328004cef3dbe6a9a3f54c633d5143d9391d1f22ea296f87e7"
RETAINED_RESERVATION = "088fc779-4acc-4cec-9e50-c6d52f4af183"


def sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"unsafe audit input: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_set_digest(root: Path) -> str:
    paths = sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink())
    if len(paths) != 63 or any(p.is_symlink() for p in paths):
        raise ValueError("last continuation file inventory differs")
    entries = [(p.relative_to(root).as_posix(), sha(p)) for p in paths]
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


def git_head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def require_clean(root: Path) -> None:
    if subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root, text=True
    ):
        raise ValueError(f"pinned checkout is dirty: {root}")


@dataclass(frozen=True)
class CallEvidence:
    start: ModelCallStarted
    completed: ModelCallCompleted
    request: dict[str, object]
    response: dict[str, object]


@dataclass(frozen=True)
class CellEvidence:
    assignment: dict[str, object]
    row: dict[str, object]
    record: dict[str, object] | None
    trace: tuple[object, ...]
    calls: tuple[CallEvidence, ...]
    setup_trace: tuple[object, ...] = ()


@dataclass(frozen=True)
class AuditSnapshot:
    manifest: dict[str, object]
    cells: tuple[CellEvidence, ...]
    boundary: dict[str, object]
    private_root: Path

    def public_counts(self) -> dict[str, object]:
        rows = [cell.row for cell in self.cells]
        return {
            "assigned": len(rows),
            "started": sum(row["started"] is True for row in rows),
            "terminal": sum(
                any(isinstance(e, RunCompleted) for e in cell.trace) for cell in self.cells
            ),
            "trace_auditable": sum(row["trace_auditable"] is True for row in rows),
            "score_valid": sum(row["score_valid"] is True for row in rows),
            "resource_auditable": sum(row["resource_auditable"] is True for row in rows),
            "stage_observable": sum(
                row["trace_auditable"] is True and row.get("root_diagnostics") is not None
                for row in rows
            ),
            "started_missing_orders": [
                row["order"] for row in rows if row["started"] and not row["trace_auditable"]
            ],
            "unstarted_orders": [row["order"] for row in rows if not row["started"]],
        }


async def load_snapshot(project_root: Path) -> AuditSnapshot:
    """Admit only the exact stopped prefix; database connections force read-only mode."""
    # Historical stop verifiers are intentionally imported here: their exact
    # hashes are part of the admission contract, not reimplemented heuristically.
    from m671_cell109_interrupted_stop import (  # type: ignore[import-not-found]
        INTERRUPTED_RECEIPT_SHA256,
        INTERRUPTED_RUN_ID,
        INTERRUPTED_TRACE_SHA256,
        stopped_continuation_records,
    )
    from m671_cell113_environment_stop import (  # type: ignore[import-not-found]
        FAILED_RUN_ID as CELL113_RUN_ID,
    )
    from m671_cell113_environment_stop import FAILED_TRACE_SHA256 as CELL113_TRACE_SHA
    from m671_cell113_environment_stop import stopped_cell113_records
    from m671_cell116_environment_stop import (  # type: ignore[import-not-found]
        FAILED_RUN_ID as CELL116_RUN_ID,
    )
    from m671_cell116_environment_stop import FAILED_TRACE_SHA256 as CELL116_TRACE_SHA
    from m671_cell116_environment_stop import stopped_cell116_records
    from m671_resume_after_cell113_environment import known_reservation

    project_root = project_root.resolve()
    execution = project_root / ".offsecgym/m671-cell116-paid-execution"
    source = project_root / ".offsecgym/m671-source-checkout"
    protocol = project_root / ".offsecgym/m671-protocol-checkout"
    private = project_root / ".offsecgym/m671-feasibility-v1"
    continuation = private / "cell116-continuation"
    manifest_path = execution / "experiments/manifests/m671-factorial-feasibility-v1.json"
    if git_head(source) != SOURCE_COMMIT or git_head(protocol) != PROTOCOL_COMMIT:
        raise ValueError("frozen source or protocol commit differs")
    for root in (execution, source, protocol):
        require_clean(root)
    if sha(manifest_path) != MANIFEST_SHA:
        raise ValueError("manifest digest differs")
    manifest = json.loads(manifest_path.read_bytes())
    verify_manifest(manifest, root=protocol)
    if manifest["assignment_sha256"] != ASSIGNMENT_SHA:
        raise ValueError("assignment digest differs")
    if (
        sha(continuation / "journal.jsonl") != LAST_JOURNAL_SHA
        or sha(private / "cell116-continuation.log") != LAST_LOG_SHA
        or sha(continuation / "failed-cell-118.json") != LAST_FAILURE_SHA
        or file_set_digest(continuation) != LAST_FILE_SET_SHA
    ):
        raise ValueError("last stopped evidence differs")
    approval = private / "cell116-paid-approval.json"
    if (
        sha(approval) != "9be3e874027e2bca95767386df988d3a08e350675c35a36db820c5b1fae527e5"
        or stat.S_IMODE(approval.stat().st_mode) != 0o600
    ):
        raise ValueError("consumed private approval pin differs")
    first = stopped_continuation_records(manifest, private)
    middle, failed113 = stopped_cell113_records(manifest, private)
    last, failed116 = stopped_cell116_records(manifest, private)
    journal = [
        json.loads(line) for line in (continuation / "journal.jsonl").read_bytes().splitlines()
    ]
    if (
        len(journal) != 4
        or [line.get("type") for line in journal]
        != ["batch_started", "cell_started", "cell_completed", "cell_started"]
        or [line.get("order") for line in journal[1:]] != [117, 117, 118]
        or journal[1]["run_id"] != journal[2]["run_id"]
    ):
        raise ValueError("last continuation journal is not the frozen prefix")
    failed118 = json.loads((continuation / "failed-cell-118.json").read_bytes())
    if (
        failed118["order"] != 118
        or failed118["run_id"] != journal[3]["run_id"]
        or failed118["terminal_status"] != "environment_failed"
        or failed118["model_call_starts"] != 0
    ):
        raise ValueError("cell 118 failure receipt differs")
    db_file = private / "db-url.txt"
    if db_file.is_symlink() or stat.S_IMODE(db_file.stat().st_mode) & 0o077:
        raise ValueError("private database URL file is unsafe")
    engine = create_async_engine(
        db_file.read_text().strip(),
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    store = PostgresEventStore(engine)
    artifacts = ModelCallArtifacts(private / "range-state")
    results: list[CellEvidence] = []
    try:
        await known_reservation(engine)
        for assignment in manifest["cells"]:
            order = assignment["order"]
            if order >= 119:
                results.append(
                    CellEvidence(
                        assignment, _missing(assignment, "unstarted", started=False), None, (), ()
                    )
                )
                continue
            if order == 109:
                record = first[-1]
                run_id = INTERRUPTED_RUN_ID
            elif order == 113:
                record = failed113
                run_id = CELL113_RUN_ID
            elif order == 116:
                record = failed116
                run_id = CELL116_RUN_ID
            elif order == 118:
                record = journal[3]
                run_id = UUID(failed118["run_id"])
            else:
                record = (
                    first[order - 1]
                    if order < 109
                    else middle[order - 110]
                    if order <= 112
                    else last[order - 114]
                    if order <= 115
                    else journal[2]
                )
                run_id = UUID(record["run_id"])
            trace = await store.read_run(run_id)
            expected_trace = (
                CELL113_TRACE_SHA
                if order == 113
                else CELL116_TRACE_SHA
                if order == 116
                else failed118["trace_sha256"]
                if order == 118
                else record.get("trace_sha256")
            )
            if order == 109:
                receipt = private / "range-state/critical_checkpoints" / f"{run_id.hex}.json"
                if (
                    trace_digest(trace) != INTERRUPTED_TRACE_SHA256
                    or sha(receipt) != INTERRUPTED_RECEIPT_SHA256
                ):
                    raise ValueError("cell 109 interrupted evidence differs")
            elif trace_digest(trace) != expected_trace:
                raise ValueError(f"source trace differs for assigned order {order}")
            if order in (109, 113, 116, 118):
                row = _missing(assignment, "retained started missingness", started=True)
                if order != 109:
                    if (
                        len(trace) != 2
                        or not isinstance(trace[0], RunStarted)
                        or not isinstance(trace[1], RunCompleted)
                        or trace[1].status != "environment_failed"
                        or any(isinstance(event, ModelCallStarted) for event in trace)
                    ):
                        raise ValueError(f"started failure differs for order {order}")
                    row = {**row, "terminal_status": "environment_failed", "run_id": str(run_id)}
            else:
                row, _ = await _cell(
                    assignment,
                    record,
                    events=store,
                    state_root=private / "range-state",
                    manifest=manifest,
                )
                if row["trace_auditable"] is not True:
                    raise ValueError(f"previously auditable cell changed: {order}")
            starts = [event for event in trace if isinstance(event, ModelCallStarted)]
            completed = {
                event.call_id: event for event in trace if isinstance(event, ModelCallCompleted)
            }
            calls = []
            for start in starts:
                if start.call_id not in completed:
                    continue
                done = completed[start.call_id]
                calls.append(
                    CallEvidence(
                        start,
                        done,
                        artifacts.read_verified(
                            run_id,
                            start.call_id,
                            "request",
                            start.request_artifact_id,
                            start.request_sha256,
                        ),
                        artifacts.read_verified(
                            run_id,
                            done.call_id,
                            "response",
                            done.response_artifact_id,
                            done.response_sha256,
                        ),
                    )
                )
            setup_trace: tuple[object, ...] = ()
            if row["trace_auditable"] is True and assignment["root_id"] == "B3-CANCELLED-JOB":
                receipt_path = private / "range-state/critical_checkpoints" / f"{run_id.hex}.json"
                receipt = json.loads(receipt_path.read_bytes())
                setup_trace = tuple(await store.read_run(UUID(receipt["setup_run_id"])))
            results.append(
                CellEvidence(assignment, row, record, tuple(trace), tuple(calls), setup_trace)
            )
        async with engine.connect() as connection:
            run_count = (
                await connection.execute(select(func.count()).select_from(runs))
            ).scalar_one()
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
        if (run_count, event_count, active_actions) != (233, 189225, 0):
            raise ValueError("authoritative database inventory changed")
        counts = AuditSnapshot(manifest, tuple(results), {}, private).public_counts()
        if (
            counts["assigned"],
            counts["started"],
            counts["trace_auditable"],
            counts["score_valid"],
        ) != (128, 118, 114, 110):
            raise ValueError("assigned/started/auditable boundary changed")
        boundary = {
            "manifest_sha256": MANIFEST_SHA,
            "assignment_sha256": ASSIGNMENT_SHA,
            "source_commit": SOURCE_COMMIT,
            "protocol_commit": PROTOCOL_COMMIT,
            "stopped_journal_sha256": LAST_JOURNAL_SHA,
            "stopped_log_sha256": LAST_LOG_SHA,
            "stopped_file_set_sha256": LAST_FILE_SET_SHA,
            "failed_cell_118_sha256": LAST_FAILURE_SHA,
            "database_runs": run_count,
            "database_events": event_count,
            "active_action_reservations": active_actions,
            "retained_model_reservation_call_id": RETAINED_RESERVATION,
        }
        return AuditSnapshot(manifest, tuple(results), boundary, private)
    finally:
        await engine.dispose()
