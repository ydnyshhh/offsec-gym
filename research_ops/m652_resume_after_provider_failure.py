"""Continue unstarted M6.5.2 cells after one retained provider rate limit.

This wrapper verifies the stopped cell against PostgreSQL, then invokes the
already pinned budget-preflight continuation. It never retries or scores the
provider-failed cell, and a subsequent provider failure still stops collection.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from m652_resume_after_preflight import _journal_records, resume
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import unscored_run
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

PROTOCOL = "m652-single-provider-failure-retention-v1"
FROZEN_MANIFEST_SHA256 = "2afc5caaea1cfc330ea507783e4b8900fbd1255cc99e03f88bcf34b4a240197b"
FROZEN_STOP_JOURNAL_SHA256 = "775073bdc84ab7ff9de0c6f35b6dc0b23e09eeb58900e440eb0092948b1bac93"
FROZEN_RUN_ID = "232a0788-aa12-4f7c-8e5c-016fe6acbada"
FROZEN_CELL_ID = "d68ec416469f9812"
FROZEN_CELL_ORDER = 16


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def provider_failure_pattern(trace: list[object]) -> bool:
    """Accept only one terminal 429 before any further model or gateway work."""
    if [getattr(item, "sequence_number", None) for item in trace] != list(range(1, len(trace) + 1)):
        return False
    failures = [item for item in trace if isinstance(item, ModelCallFailed)]
    endings = [item for item in trace if isinstance(item, RunCompleted)]
    if (
        len(failures) != 1
        or failures[0].http_status != 429
        or failures[0].reason_code != "provider_rate_limited"
        or len(endings) != 1
        or endings[0].status != "provider_failed"
        or endings[0].sequence_number != len(trace)
    ):
        return False
    failed_sequence = failures[0].sequence_number
    return not any(
        isinstance(item, (ActionRequested, ModelCallStarted, ModelCallCompleted))
        and item.sequence_number > failed_sequence
        for item in trace
    )


async def reconcile_retained_provider_failure(
    journal_path: Path, state_dir: Path, database_url: str, endpoint: tuple[str, str]
) -> dict[str, object]:
    """Check the one stopped cell against the authoritative source stream."""
    records = _journal_records(journal_path)
    if (
        _sha(journal_path.read_bytes()) != FROZEN_STOP_JOURNAL_SHA256
        or len(records) != 33
        or sum(item.get("type") == "cell_completed" for item in records) != FROZEN_CELL_ORDER
    ):
        raise ValueError("provider-failure stop journal differs from the pinned record")
    record = records[-1]
    if (
        record.get("type") != "cell_completed"
        or record.get("order") != FROZEN_CELL_ORDER
        or record.get("cell_id") != FROZEN_CELL_ID
        or record.get("source_run_id") != FROZEN_RUN_ID
        or record.get("source_status") != "provider_failed"
        or record.get("branches") != {}
        or record.get("audit") is not None
        or record.get("reporting_prefix_rejections") != []
    ):
        raise ValueError("retained provider-failure cell has an unexpected journal shape")
    engine = create_async_engine(database_url)
    try:
        events = PostgresEventStore(engine)
        trace = list(await events.read_run(UUID(FROZEN_RUN_ID)))
        raw = Path(record["source_trace_path"]).read_bytes()
        if _sha(raw) != record["source_trace_sha256"] or [
            item.model_dump(mode="json") for item in trace
        ] != json.loads(raw):
            raise ValueError("provider-failed trace differs from authoritative PostgreSQL")
        if not provider_failure_pattern(trace):
            raise ValueError("provider-failed source does not match the pinned 429 pattern")
        completions = [item for item in trace if isinstance(item, ModelCallCompleted)]
        if not completions or {
            (item.resolved_model_revision, item.resolved_upstream_provider) for item in completions
        } != {endpoint}:
            raise ValueError("provider-failed source model endpoint differs")
        findings = tuple(item.finding for item in trace if isinstance(item, FindingSubmitted))
        verdicts = tuple(item.result for item in trace if isinstance(item, FindingValidated))
        evaluation = unscored_run(
            "provider_failed",
            len(findings),
            validated_count=sum(item.status == "validated" for item in verdicts),
            inconclusive=sum(item.status == "inconclusive" for item in verdicts),
        )
        if evaluation.model_dump(mode="json") != record["source_evaluation"]:
            raise ValueError("provider-failed source evaluation does not replay")
        async with engine.connect() as connection:
            branch_count = (
                await connection.execute(
                    select(func.count())
                    .select_from(reporting_branches)
                    .where(reporting_branches.c.source_run_id == UUID(FROZEN_RUN_ID))
                )
            ).scalar_one()
        if branch_count:
            raise ValueError("provider-failed source unexpectedly has reporting branches")
        return {
            "retained_order": FROZEN_CELL_ORDER,
            "events": len(trace),
            "http_status": 429,
            "score_valid": False,
            "reporting_branches": 0,
            "journal_sha256": FROZEN_STOP_JOURNAL_SHA256,
            "state_dir": str(state_dir),
        }
    finally:
        await engine.dispose()


async def continue_sample(
    root: Path,
    manifest_path: Path,
    preflight_amendment_path: Path,
    provider_amendment_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict[str, object]:
    amendment = json.loads(provider_amendment_path.read_text())
    if (
        amendment.get("protocol") != PROTOCOL
        or amendment.get("sample_manifest_sha256") != FROZEN_MANIFEST_SHA256
        or amendment.get("stop_journal_sha256") != FROZEN_STOP_JOURNAL_SHA256
        or amendment.get("stopped_run_id") != FROZEN_RUN_ID
        or amendment.get("resume_script_sha256") != _sha(Path(__file__).read_bytes())
        or _sha(manifest_path.read_bytes()) != FROZEN_MANIFEST_SHA256
    ):
        raise ValueError("provider-failure amendment or frozen manifest differs")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    manifest = json.loads(manifest_path.read_text())
    endpoint = (
        manifest["expected_selected_endpoint"]["revision"],
        manifest["expected_selected_endpoint"]["upstream_provider"],
    )
    reconciliation = await reconcile_retained_provider_failure(
        journal_path, state_dir, database_url, endpoint
    )
    print(json.dumps({"provider_failure_reconciled": reconciliation}, sort_keys=True), flush=True)
    return await resume(root, manifest_path, preflight_amendment_path, journal_path, state_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--preflight-amendment", type=Path, required=True)
    parser.add_argument("--provider-amendment", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                continue_sample(
                    args.repository_root,
                    args.manifest,
                    args.preflight_amendment,
                    args.provider_amendment,
                    args.journal,
                    args.state_dir,
                )
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
