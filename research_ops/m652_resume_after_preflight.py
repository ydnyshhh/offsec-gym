"""Resume frozen M6.5.2 cells after a narrowly verified probe preflight stop.

This controller invokes the pinned collector one unstarted cell at a time.
It never edits the source manifest, model policy, journal, or prior events.
Only a score-valid source with a verified post-tool checkpoint followed by
an unaffordable next request may be retained as an ineligible prefix.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import evaluate_run
from offsecgym.research.m652_checkpoint import ProbeCheckpointStore
from offsecgym.research.m652_execute import execute, verify_manifest
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import ValidationContext
from offsecgym.schemas.events import (
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    RangeStarted,
    RunCompleted,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import reporting_branches

PROTOCOL = "m652-budget-preflight-eligibility-v1"
FROZEN_MANIFEST_SHA256 = "2afc5caaea1cfc330ea507783e4b8900fbd1255cc99e03f88bcf34b4a240197b"
FROZEN_STOP_JOURNAL_SHA256 = "ab4f3d3b08446d9925fe7e69444914ac4c7d69aeff818481ff791b8cf60cde23"
FROZEN_STOP_RUN_ID = "f8adfe3d-6bfb-4e3b-8f9b-ea6865b5bc46"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def budget_preflight_pattern(trace: list[object], source_sequence: int) -> bool:
    """Recognize only the checked-action-free post-checkpoint budget edge."""
    if [getattr(item, "sequence_number", None) for item in trace] != list(range(1, len(trace) + 1)):
        return False
    if source_sequence < 1 or len(trace) < source_sequence + 5:
        return False
    boundary = trace[source_sequence : source_sequence + 4]
    if [getattr(item, "type", None) for item in boundary] != [
        "probe_checkpoint_saved",
        "context_retrieved",
        "model_reservation_rejected",
        "reporting_prefix_rejected",
    ]:
        return False
    if (
        getattr(boundary[2], "reason_code", None) != "model_token_budget_exhausted"
        or getattr(boundary[3], "reason_code", None) != "invalid_source_prefix_ValueError"
    ):
        return False
    later = trace[source_sequence + 4 :]
    if any(
        getattr(item, "type", None)
        not in {
            "finding_validated",
            "run_completed",
            "coverage_updated",
            "coverage_lease_released",
        }
        for item in later
    ):
        return False
    endings = [item for item in later if getattr(item, "type", None) == "run_completed"]
    return len(endings) == 1 and getattr(endings[0], "status", None) == "budget_exhausted"


def _journal_records(path: Path) -> list[dict[str, object]]:
    raw_lines = path.read_bytes().splitlines(keepends=True)
    if len(raw_lines) < 5 or _sha(b"".join(raw_lines[:5])) != FROZEN_STOP_JOURNAL_SHA256:
        raise ValueError("the original two-cell stop journal differs from its frozen SHA")
    records = [json.loads(line) for line in raw_lines]
    if [item.get("type") for item in records[:5]] != [
        "batch_started",
        "cell_started",
        "cell_completed",
        "cell_started",
        "cell_completed",
    ]:
        raise ValueError("the original two-cell stop journal has a different order")
    if records[4].get("source_run_id") != FROZEN_STOP_RUN_ID:
        raise ValueError("the stopped source run differs")
    started: set[str] = set()
    completed: set[str] = set()
    for record in records[1:]:
        cell_id = record.get("cell_id")
        if not isinstance(cell_id, str):
            raise ValueError("journal cell has no ID")
        if record.get("type") == "cell_started":
            if cell_id in started or cell_id in completed:
                raise ValueError("journal repeats a cell start")
            started.add(cell_id)
        elif record.get("type") == "cell_completed":
            if cell_id not in started:
                raise ValueError("journal completion has no matching start")
            started.remove(cell_id)
            completed.add(cell_id)
        else:
            raise ValueError("journal has an unexpected record type")
    if started:
        raise ValueError("interrupted cell requires separate event-store reconciliation")
    return records


async def _reconcile_budget_prefix(
    events: PostgresEventStore,
    state_dir: Path,
    record: dict[str, object],
    expected_endpoint: tuple[str, str],
) -> None:
    if (
        record.get("reporting_prefix_rejections") != ["invalid_source_prefix_ValueError"]
        or record.get("source_status") != "budget_exhausted"
        or not record.get("source_evaluation", {}).get("score_valid")
        or record.get("branches") != {}
        or record.get("audit") is not None
    ):
        raise ValueError("cell does not have the approved ineligible-prefix shape")
    run_id = UUID(record["source_run_id"])
    trace = list(await events.read_run(run_id))
    saved_path = Path(record["source_trace_path"])
    raw = saved_path.read_bytes()
    if _sha(raw) != record["source_trace_sha256"] or [
        item.model_dump(mode="json") for item in trace
    ] != json.loads(raw):
        raise ValueError("ineligible source trace differs from authoritative PostgreSQL")
    checkpoint = await ProbeCheckpointStore(events, state_dir).load_latest(run_id)
    if not budget_preflight_pattern(trace, checkpoint.source_sequence):
        raise ValueError("post-checkpoint events differ from the approved preflight pattern")
    completions = [item for item in trace if isinstance(item, ModelCallCompleted)]
    if not 0 < len(completions) < 10 or {
        (item.resolved_model_revision, item.resolved_upstream_provider) for item in completions
    } != {expected_endpoint}:
        raise ValueError("ineligible source model calls or endpoint differ")
    async with events.engine.connect() as connection:
        branch_count = (
            await connection.execute(
                select(func.count())
                .select_from(reporting_branches)
                .where(reporting_branches.c.source_run_id == run_id)
            )
        ).scalar_one()
    if branch_count:
        raise ValueError("ineligible source unexpectedly has a reporting branch")
    ranges = [item for item in trace if isinstance(item, RangeStarted)]
    endings = [item for item in trace if isinstance(item, RunCompleted)]
    if len(ranges) != 1 or len(endings) != 1:
        raise ValueError("ineligible source lacks a unique range or terminal event")
    context = ValidationContext(
        run_id=run_id,
        range_instance_id=ranges[0].range_instance_id,
        range_generation=ranges[0].range_generation,
        build_id=ranges[0].build_id,
    )
    oracle = StateOracleStore(StateStore(state_dir)).load_for_context(context)
    evaluation = evaluate_run(
        tuple(item.finding for item in trace if isinstance(item, FindingSubmitted)),
        tuple(item.result for item in trace if isinstance(item, FindingValidated)),
        oracle,
        status=endings[0].status,
    )
    if evaluation.model_dump(mode="json") != record["source_evaluation"]:
        raise ValueError("ineligible source score differs from oracle replay")


async def _reconcile_existing(
    records: list[dict[str, object]], state_dir: Path, endpoint: tuple[str, str]
) -> None:
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    engine = create_async_engine(database_url)
    try:
        events = PostgresEventStore(engine)
        for record in records:
            if record.get("type") != "cell_completed":
                continue
            reasons = record.get("reporting_prefix_rejections")
            if reasons in ([], ["probe_status_not_eligible"]):
                continue
            if reasons != ["invalid_source_prefix_ValueError"]:
                raise ValueError("prior cell has an unapproved prefix rejection")
            await _reconcile_budget_prefix(events, state_dir, record, endpoint)
    finally:
        await engine.dispose()


async def resume(
    root: Path, manifest_path: Path, amendment_path: Path, journal_path: Path, state_dir: Path
) -> dict[str, object]:
    manifest = verify_manifest(root, manifest_path)
    amendment = json.loads(amendment_path.read_text())
    if (
        amendment.get("protocol") != PROTOCOL
        or _sha(manifest_path.read_bytes()) != FROZEN_MANIFEST_SHA256
        or amendment.get("sample_manifest_sha256") != FROZEN_MANIFEST_SHA256
        or amendment.get("stop_journal_sha256") != FROZEN_STOP_JOURNAL_SHA256
        or amendment.get("stopped_run_id") != FROZEN_STOP_RUN_ID
        or _sha(Path(__file__).read_bytes()) != amendment.get("resume_script_sha256")
        or manifest["cumulative_estimated_cost_stop_usd"] != 108.0
    ):
        raise ValueError("M6.5.2 eligibility amendment or frozen manifest differs")
    endpoint = (
        manifest["expected_selected_endpoint"]["revision"],
        manifest["expected_selected_endpoint"]["upstream_provider"],
    )
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = journal_path.parent / "resume.lock"
    with lock_path.open("a+b") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("another M6.5.2 resume controller holds the lock") from exc
        records = _journal_records(journal_path)
        await _reconcile_existing(records, state_dir, endpoint)
        while True:
            before = sum(item.get("type") == "cell_completed" for item in records)
            if before == len(manifest["cells"]):
                return {"complete": True, "completed_cells": before}
            try:
                result = await execute(
                    root,
                    manifest_path,
                    journal_path,
                    state_dir,
                    max_estimated_usd=108.0,
                    max_cells=1,
                )
            except ValueError as exc:
                if str(exc) != "M6.5.2 prefix integrity failed and was retained without retry":
                    raise
                records = _journal_records(journal_path)
                after = sum(item.get("type") == "cell_completed" for item in records)
                if after != before + 1 or records[-1].get("type") != "cell_completed":
                    raise ValueError("prefix stop did not leave one completed cell") from exc
                await _reconcile_existing(records[-1:], state_dir, endpoint)
                print(
                    json.dumps(
                        {
                            "retained_ineligible_cell": records[-1]["cell_id"],
                            "completed_cells": after,
                        }
                    ),
                    flush=True,
                )
                continue
            records = _journal_records(journal_path)
            after = sum(item.get("type") == "cell_completed" for item in records)
            if after != before + 1:
                raise ValueError("resume did not complete exactly one new cell")
            print(
                json.dumps(
                    {
                        "completed_cells": after,
                        "estimated_cost_usd": result["estimated_cost_usd"],
                        "complete": result["complete"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if result["complete"]:
                return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--amendment", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                resume(
                    args.repository_root,
                    args.manifest,
                    args.amendment,
                    args.journal,
                    args.state_dir,
                )
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
