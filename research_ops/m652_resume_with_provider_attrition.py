"""Continue unstarted M6.5.2 cells with bounded, no-retry provider attrition.

The frozen collector still stops on each score-invalid cell. This controller
reconciles only transient provider failures from PostgreSQL, retains their
unscored cells, and then invokes the pinned collector for the next cell.
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

from m652_resume_after_preflight import _journal_records, _reconcile_existing
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import unscored_run
from offsecgym.research.m652_execute import execute, verify_manifest
from offsecgym.schemas.events import (
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    RunCompleted,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.tables import reporting_branches

PROTOCOL = "m652-bounded-provider-attrition-v1"
FROZEN_MANIFEST_SHA256 = "2afc5caaea1cfc330ea507783e4b8900fbd1255cc99e03f88bcf34b4a240197b"
FROZEN_STOP_JOURNAL_SHA256 = "4013c4b450ba03189c5f4ef68524862ac18fdba27a5ff0aa8f75021cddafb2dc"
KNOWN_FAILED_RUNS = {
    16: "232a0788-aa12-4f7c-8e5c-016fe6acbada",
    27: "c2ce2afd-ccc3-4df2-bfb0-68f6d6210aba",
}
MAX_PROVIDER_FAILED_CELLS = 4


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def transient_provider_failure_pattern(trace: list[object]) -> bool:
    """Allow one transport/rate-limit/server failure, with no later work."""
    if [getattr(item, "sequence_number", None) for item in trace] != list(range(1, len(trace) + 1)):
        return False
    failures = [item for item in trace if isinstance(item, ModelCallFailed)]
    endings = [item for item in trace if isinstance(item, RunCompleted)]
    if len(failures) != 1 or len(endings) != 1 or endings[0].status != "provider_failed":
        return False
    failure = failures[0]
    allowed = (
        (failure.reason_code == "provider_unavailable" and failure.http_status is None)
        or (failure.reason_code == "provider_rate_limited" and failure.http_status == 429)
        or (failure.reason_code == "provider_timeout" and failure.http_status == 408)
        or (
            failure.reason_code == "provider_server_error"
            and failure.http_status is not None
            and 500 <= failure.http_status <= 599
        )
    )
    if not allowed or endings[0].sequence_number <= failure.sequence_number:
        return False
    started = [
        item
        for item in trace
        if isinstance(item, ModelCallStarted) and item.call_id == failure.call_id
    ]
    if len(started) != 1 or started[0].sequence_number >= failure.sequence_number:
        return False
    return not any(
        isinstance(item, (ActionRequested, ModelCallStarted, ModelCallCompleted))
        and item.sequence_number > failure.sequence_number
        for item in trace
    )


async def reconcile_provider_failed_source(
    events: PostgresEventStore, record: dict[str, object], endpoint: tuple[str, str]
) -> dict[str, object]:
    """Replay one unscored source from authoritative events without retry."""
    if (
        record.get("source_status") != "provider_failed"
        or record.get("source_evaluation", {}).get("score_valid") is not False
        or record.get("reporting_prefix_rejections") != []
        or record.get("branches") != {}
        or record.get("audit") is not None
    ):
        raise ValueError("source does not have the approved provider-failure journal shape")
    run_id = UUID(record["source_run_id"])
    trace = list(await events.read_run(run_id))
    raw = Path(record["source_trace_path"]).read_bytes()
    if _sha(raw) != record["source_trace_sha256"] or [
        item.model_dump(mode="json") for item in trace
    ] != json.loads(raw):
        raise ValueError("provider-failed source trace differs from PostgreSQL")
    if not transient_provider_failure_pattern(trace):
        raise ValueError("source is not an accepted transient provider failure")
    completed = [item for item in trace if isinstance(item, ModelCallCompleted)]
    if completed and {
        (item.resolved_model_revision, item.resolved_upstream_provider) for item in completed
    } != {endpoint}:
        raise ValueError("provider-failed source selected endpoint differs")
    findings = tuple(item.finding for item in trace if isinstance(item, FindingSubmitted))
    verdicts = tuple(item.result for item in trace if isinstance(item, FindingValidated))
    evaluation = unscored_run(
        "provider_failed",
        len(findings),
        validated_count=sum(item.status == "validated" for item in verdicts),
        inconclusive=sum(item.status == "inconclusive" for item in verdicts),
    )
    if evaluation.model_dump(mode="json") != record["source_evaluation"]:
        raise ValueError("provider-failed source score does not replay")
    async with events.engine.connect() as connection:
        branch_count = (
            await connection.execute(
                select(func.count())
                .select_from(reporting_branches)
                .where(reporting_branches.c.source_run_id == run_id)
            )
        ).scalar_one()
    if branch_count:
        raise ValueError("provider-failed source unexpectedly has reporting branches")
    failure = next(item for item in trace if isinstance(item, ModelCallFailed))
    return {
        "order": record["order"],
        "run_id": str(run_id),
        "event_count": len(trace),
        "reason_code": failure.reason_code,
        "http_status": failure.http_status,
        "score_valid": False,
        "reporting_branches": 0,
    }


async def _reconcile_provider_records(
    records: list[dict[str, object]], endpoint: tuple[str, str], database_url: str
) -> list[dict[str, object]]:
    engine = create_async_engine(database_url)
    try:
        events = PostgresEventStore(engine)
        verified = []
        for record in records:
            if (
                record.get("type") == "cell_completed"
                and record.get("source_status") == "provider_failed"
            ):
                verified.append(await reconcile_provider_failed_source(events, record, endpoint))
        return verified
    finally:
        await engine.dispose()


async def continue_sample(
    root: Path,
    manifest_path: Path,
    amendment_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict[str, object]:
    manifest = verify_manifest(root, manifest_path)
    amendment = json.loads(amendment_path.read_text())
    if (
        amendment.get("protocol") != PROTOCOL
        or amendment.get("sample_manifest_sha256") != FROZEN_MANIFEST_SHA256
        or amendment.get("stop_journal_sha256") != FROZEN_STOP_JOURNAL_SHA256
        or amendment.get("known_failed_runs")
        != {str(order): run_id for order, run_id in KNOWN_FAILED_RUNS.items()}
        or amendment.get("max_provider_failed_cells") != MAX_PROVIDER_FAILED_CELLS
        or amendment.get("resume_script_sha256") != _sha(Path(__file__).read_bytes())
        or _sha(manifest_path.read_bytes()) != FROZEN_MANIFEST_SHA256
        or manifest["cumulative_estimated_cost_stop_usd"] != 108.0
    ):
        raise ValueError("bounded provider-attrition amendment or frozen manifest differs")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    endpoint = (
        manifest["expected_selected_endpoint"]["revision"],
        manifest["expected_selected_endpoint"]["upstream_provider"],
    )
    lock_path = journal_path.parent / "resume.lock"
    with lock_path.open("a+b") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("another M6.5.2 controller holds the lock") from exc
        records = _journal_records(journal_path)
        if (
            _sha(journal_path.read_bytes()) != FROZEN_STOP_JOURNAL_SHA256
            or len(records) != 55
            or sum(item.get("type") == "cell_completed" for item in records) != 27
        ):
            raise ValueError("27-cell stop journal differs from the pinned record")
        await _reconcile_existing(records, state_dir, endpoint)
        verified = await _reconcile_provider_records(records, endpoint, database_url)
        if {item["order"]: item["run_id"] for item in verified} != KNOWN_FAILED_RUNS:
            raise ValueError("stopped provider-failure runs differ")
        print(json.dumps({"retained_provider_failures": verified}, sort_keys=True), flush=True)
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
                if str(exc) not in {
                    "M6.5.2 prefix integrity failed and was retained without retry",
                    "M6.5.2 source is score-invalid and retained without retry",
                }:
                    raise
                records = _journal_records(journal_path)
                after = sum(item.get("type") == "cell_completed" for item in records)
                if after != before + 1 or records[-1].get("type") != "cell_completed":
                    raise ValueError("collector stop did not leave one completed cell") from exc
                await _reconcile_existing(records[-1:], state_dir, endpoint)
                if records[-1].get("source_status") == "provider_failed":
                    latest = await _reconcile_provider_records(records[-1:], endpoint, database_url)
                    if len(latest) != 1:
                        raise ValueError("provider-failed cell was not reconciled") from exc
                    verified.extend(latest)
                    print(
                        json.dumps({"retained_provider_failure": latest[0]}, sort_keys=True),
                        flush=True,
                    )
                    if len(verified) > MAX_PROVIDER_FAILED_CELLS:
                        raise ValueError(
                            "M6.5.2 provider attrition exceeded the pinned ceiling"
                        ) from exc
                elif records[-1].get("reporting_prefix_rejections") == [
                    "invalid_source_prefix_ValueError"
                ]:
                    print(json.dumps({"retained_budget_prefix": records[-1]["order"]}), flush=True)
                else:
                    raise ValueError("collector stop is outside the amended policy") from exc
                continue
            records = _journal_records(journal_path)
            after = sum(item.get("type") == "cell_completed" for item in records)
            if after != before + 1:
                raise ValueError("controller did not complete exactly one new cell")
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
                continue_sample(
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
