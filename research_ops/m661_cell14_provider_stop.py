"""Read-only admission of the exact M6.6.1 provider-health stop at cell 14.

The frozen postcheck replays all authoritative evidence before reaching the
intentionally absent pair-seven receipt. No study file is created here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from m661_cell1_recovery import STOP_JOURNAL_SHA256 as ORIGINAL_JOURNAL_SHA256
from m661_cell4_recovery_postcheck import _new_records, postcheck
from m661_cell4_stop import CONTINUATION_JOURNAL_SHA256
from m661_feasibility_collect import (
    CAP,
    CELL_RESERVATION,
    EXPECTED_MANIFEST_SHA256,
    REVISION,
    UPSTREAM,
    _approval_and_manifest,
    _health_decision,
    _sha256,
)
from m661_resume_after_cell4_permission import _no_collector_running

STOP_JOURNAL_SHA256 = "d2988dbe83364cb89a46fec1b5812d90dd2f01f35379760d5aa556b9fb98fdba"
STOP_LOG_SHA256 = "ebd86476e579f931d945d052c00c8915a5d8f5e2d05527e084f3f699db6b6c97"
PAIR6_SHA256 = "a18952664a5dddf8377dc1f9185dd21779ddadd0bf44cfe0641621b5056a4d05"
PREFLIGHT7_SHA256 = "1832818562e91db607e60a92fd189a9e6e85c8bbafe1fe436732207ea02f9f3b"
STOP_ESTIMATED_COST = Decimal("4.202310")
STOP_CONSERVATIVE_COST = Decimal("7.646460")
FAILED_RUNS = (
    "de0560ac-deec-479f-be19-00b073426d40",
    "c4c5f58a-8cb4-444c-9a0b-c292f50591d0",
)
FAILED_TRACE_SHA256 = (
    "d674656dbd6ded49885058befe45477adaf65e5ca6b31cb10632bc73daef1103",
    "3addd674101fadec3ac93ff686b03a0f2b5ee8a841e3b8c6e33a394b5d11a812",
)
FAILED_IDENTITY_SHA256 = (
    "c927cae77e5ff69ed0143930d7be686208769b83ef53dca7ac462953ab776da2",
    "876064fab1942e487425289e3a2150dac0bfdc1d71e6b3948341e8ab1d0d4f9a",
)


def _pinned_records(manifest: dict, private: Path) -> list[dict]:
    new_path = private / "cell4-paid-continuation-journal.jsonl"
    if _sha256(new_path.read_bytes()) != STOP_JOURNAL_SHA256:
        raise ValueError("cell-14 stop journal differs")
    records = _new_records(manifest, new_path)
    if len(records) != 10 or [item["order"] for item in records] != list(range(5, 15)):
        raise ValueError("cell-14 frozen order differs")
    if any(item["score_valid"] is not True for item in records[:8]):
        raise ValueError("retained cells 5–12 score validity differs")
    for item, run_id, trace_sha, identity_sha in zip(
        records[-2:], FAILED_RUNS, FAILED_TRACE_SHA256, FAILED_IDENTITY_SHA256, strict=True
    ):
        if (
            item["run_id"] != run_id
            or item["status"] != "provider_failed"
            or item["failure_reason"] != "provider_unavailable"
            or item["score_valid"] is not False
            or item["resource_auditable"] is not False
            or item["trace_sha256"] != trace_sha
            or item["event_identity_sha256"] != identity_sha
        ):
            raise ValueError("retained provider failure differs")
    if _health_decision(records[:-1]) != "continue" or _health_decision(records) != "pause":
        raise ValueError("frozen provider-health decision differs")
    estimated = Decimal("0.719841") + sum(
        (Decimal(item["estimated_cost_usd"]) for item in records), Decimal()
    )
    conservative = Decimal("0.719841") + sum(
        (
            Decimal(item["estimated_cost_usd"]) if item["resource_auditable"] else CELL_RESERVATION
            for item in records
        ),
        Decimal(),
    )
    if (
        estimated != STOP_ESTIMATED_COST
        or conservative != STOP_CONSERVATIVE_COST
        or conservative + CELL_RESERVATION * 82 > CAP
    ):
        raise ValueError("stopped cost or remaining reservation differs")
    return records


async def admit(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, _ = _approval_and_manifest(
        execution_root, source_root, protocol_root, manifest_path, approval_path
    )
    private = approval_path.parent
    if (
        _sha256(journal_path.read_bytes()) != ORIGINAL_JOURNAL_SHA256
        or _sha256((private / "continuation-journal.jsonl").read_bytes())
        != CONTINUATION_JOURNAL_SHA256
        or _sha256((private / "collector-cell4-paid-session.log").read_bytes()) != STOP_LOG_SHA256
        or _sha256((private / "pair-postcheck-6.json").read_bytes()) != PAIR6_SHA256
        or _sha256((private / "endpoint-preflight-7.json").read_bytes()) != PREFLIGHT7_SHA256
        or (private / "pair-postcheck-7.json").exists()
        or (private / "cell14-provider-clearance.json").exists()
        or (private / "cell14-paid-continuation-journal.jsonl").exists()
        or (private / "stages" / f"{manifest['cells'][14]['cell_id']}.json").exists()
        or (private / "traces" / f"{manifest['cells'][14]['cell_id']}.json").exists()
    ):
        raise ValueError("cell-14 stop evidence differs or cell 15 was attempted")
    endpoint = json.loads((private / "endpoint-preflight-7.json").read_bytes())
    if (
        endpoint.get("endpoint") != f"{UPSTREAM} | {REVISION}"
        or endpoint.get("input_usd_per_million_tokens") != 3.0
        or endpoint.get("output_usd_per_million_tokens") != 15.0
    ):
        raise ValueError("retained selected endpoint or price differs")
    records = _pinned_records(manifest, private)
    _no_collector_running()
    try:
        await postcheck(
            execution_root,
            source_root,
            protocol_root,
            manifest_path,
            approval_path,
            journal_path,
            state_dir,
            require_complete=False,
        )
    except FileNotFoundError as exc:
        if (
            not exc.filename
            or Path(exc.filename).resolve() != (private / "pair-postcheck-7.json").resolve()
        ):
            raise
    else:
        raise ValueError("pair seven unexpectedly closed before separate approval")
    return {
        "protocol": manifest["protocol"],
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "stopped_journal_sha256": STOP_JOURNAL_SHA256,
        "completed_new_cells": len(records),
        "next_unstarted_order": 15,
        "retained_missing_orders": [1, 4],
        "retained_provider_failed_orders": [13, 14],
        "estimated_cost_usd": str(STOP_ESTIMATED_COST),
        "conservative_cost_usd": str(STOP_CONSERVATIVE_COST),
        "pair7_receipt_present": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "execution-root",
        "source-root",
        "protocol-root",
        "manifest",
        "approval",
        "journal",
        "state-dir",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                admit(
                    args.execution_root,
                    args.source_root,
                    args.protocol_root,
                    args.manifest,
                    args.approval,
                    args.journal,
                    args.state_dir,
                )
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
