"""Reconcile the exact cell-192 transport stop before any further paid call.

The public endpoint metadata request failed before cell 193 started. This
wrapper retains the failed cell 192 and resumes only the frozen unstarted
assignments, within the health epoch that began at cell 189. It cannot run
until a separate exact approval hash is pinned after review.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

from m66_confirmatory_collect import (
    EXPECTED_APPROVAL_SHA256,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256,
    EXPECTED_PAUSE_APPROVAL_SHA256,
    EXPECTED_RATE_PAUSE_APPROVAL_SHA256,
    METADATA_RECOVERY_CLEARANCE_NAME,
    METADATA_STOP_JOURNAL_SHA256,
    PRIOR_RATE_CLEARANCE_SHA256,
    Journal,
    _approval_and_manifest,
    _fresh_endpoint_check,
    _write_json_create_only,
    collect,
)
from m66_confirmatory_postcheck import _journal_records
from m66_confirmatory_postcheck import audit as postcheck
from m66_resume_after_stage_bound import EXPECTED_STAGE_APPROVAL_SHA256

from offsecgym.research.m64_execute import _sha256

STOP_COST = Decimal("84.050823")
STOP_LOG_SHA256 = "4079246eb8e61d4fbb84681250332dab6150f4374e544a13460adce74237ed7f"
PAIR96_SHA256 = "8c394047f27284bcd76d6449cb606b0e8c8bc1c70f6a3843088dc4715e1f4cb7"
FAILED_CELL_ID = "1808e1facc145d88"
FAILED_RUN_ID = "3e722a70-fde3-491c-bd0e-d7d74b4e2c47"
RETAINED_INVALID_ORDERS = [89, 90, 170, 186, 187, 188, 192]
PROTOCOL = "m66-confirmatory-v2-metadata-transport-recovery-v1"


def _require_metadata_approval(path: Path) -> str:
    """A zero pin or a mismatched private receipt permits no paid work."""
    if EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256 == "0" * 64 or not path.is_file():
        raise ValueError("metadata recovery lacks separate exact paid-call approval")
    raw = path.read_bytes()
    digest = _sha256(raw)
    if digest != EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256:
        raise ValueError("metadata recovery approval artifact hash differs")
    approval = json.loads(raw)
    if (
        approval.get("protocol") != PROTOCOL
        or approval.get("approved_for_paid_calls") is not True
        or approval.get("approval_scope")
        != "assigned_cells_193_through_280_only_no_retry_or_replacement"
        or approval.get("original_manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("original_approval_sha256") != EXPECTED_APPROVAL_SHA256
        or approval.get("prior_pause_approval_sha256") != EXPECTED_PAUSE_APPROVAL_SHA256
        or approval.get("prior_stage_approval_sha256") != EXPECTED_STAGE_APPROVAL_SHA256
        or approval.get("prior_rate_approval_sha256") != EXPECTED_RATE_PAUSE_APPROVAL_SHA256
        or approval.get("stop_journal_sha256") != METADATA_STOP_JOURNAL_SHA256
        or Decimal(str(approval.get("cost_ceiling_usd"))) != Decimal("504")
        or approval.get("provider_health_epoch_start_order") != 189
        or approval.get("new_provider_health_epoch") is not False
        or approval.get("retained_invalid_cell_orders") != RETAINED_INVALID_ORDERS
        or approval.get("retained_agent_failed_cell_order") != 162
    ):
        raise ValueError("metadata recovery approval does not cover this exact stop")
    return digest


def _require_stop(manifest: dict, journal_path: Path) -> tuple[list[dict], Decimal]:
    raw = journal_path.read_bytes()
    if _sha256(raw) != METADATA_STOP_JOURNAL_SHA256:
        raise ValueError("metadata recovery journal differs from the audited stop")
    records = _journal_records(manifest, raw)
    cost = sum((Decimal(str(record["estimated_cost_usd"])) for record in records), Decimal(0))
    last = records[-1] if records else {}
    base = journal_path.parent
    if (
        len(records) != 192
        or cost != STOP_COST
        or last.get("cell_id") != FAILED_CELL_ID
        or last.get("run_id") != FAILED_RUN_ID
        or last.get("status") != "provider_failed"
        or last.get("failure_reason") != "provider_unavailable"
        or last.get("score_valid") is not False
        or _sha256((base / "pair-postcheck-96.json").read_bytes()) != PAIR96_SHA256
        or _sha256((base / "collector-rate-pause.log").read_bytes()) != STOP_LOG_SHA256
        or (base / "endpoint-preflight-97.json").exists()
        or (base / "pair-postcheck-97.json").exists()
        or (base / METADATA_RECOVERY_CLEARANCE_NAME).exists()
    ):
        raise ValueError("metadata recovery checkpoint has extra work or changed evidence")
    return records, cost


def _matching_endpoint(original: dict, current: dict) -> bool:
    return all(
        current.get(field) == original.get(field)
        for field in (
            "endpoint",
            "model_id",
            "provider_name",
            "status",
            "input_usd_per_million",
            "output_usd_per_million",
        )
    )


async def resume(
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    metadata_approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, approval_path
    )
    metadata_approval_sha = _require_metadata_approval(metadata_approval_path)
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("approved provider key and dedicated database URL are required")
    _, cost = _require_stop(manifest, journal_path)
    if cost >= Decimal(str(approval["cost_ceiling_usd"])):
        raise ValueError("stopped sample has reached the approved cost cap")
    base = journal_path.parent
    prior = {
        "pause-approval.json": EXPECTED_PAUSE_APPROVAL_SHA256,
        "stage-approval.json": EXPECTED_STAGE_APPROVAL_SHA256,
        "rate-approval.json": EXPECTED_RATE_PAUSE_APPROVAL_SHA256,
        "provider-rate-pause-clearance-after-cell-188.json": PRIOR_RATE_CLEARANCE_SHA256,
    }
    if any(_sha256((base / name).read_bytes()) != digest for name, digest in prior.items()):
        raise ValueError("prior approved operational provenance differs")
    original_endpoint = json.loads((base / "endpoint-preflight-96.json").read_bytes())
    endpoint = manifest["expected_selected_endpoint"]
    journal = Journal(
        journal_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=(endpoint["revision"], endpoint["upstream_provider"]),
        max_estimated_usd=float(approval["cost_ceiling_usd"]),
    )
    try:
        if _sha256(journal_path.read_bytes()) != METADATA_STOP_JOURNAL_SHA256:
            raise ValueError("journal changed after exclusive metadata recovery lock")
        checked = await postcheck(
            source_root, protocol_root, manifest_path, approval_path, journal_path, state_dir
        )
        if (
            checked["journaled_cells"] != 192
            or Decimal(str(checked["estimated_model_token_cost_usd"])) != STOP_COST
        ):
            raise ValueError("authoritative metadata stop replay differs")
        selected = _fresh_endpoint_check(manifest)
        if not _matching_endpoint(original_endpoint, selected):
            raise ValueError("selected endpoint or price differs after metadata transport stop")
        if _sha256(journal_path.read_bytes()) != METADATA_STOP_JOURNAL_SHA256:
            raise ValueError("journal changed during metadata recovery replay")
        clearance_path = base / METADATA_RECOVERY_CLEARANCE_NAME
        _write_json_create_only(
            clearance_path,
            {
                "protocol": PROTOCOL,
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "original_approval_sha256": _sha256(approval_path.read_bytes()),
                "metadata_approval_sha256": metadata_approval_sha,
                "stop_journal_sha256": METADATA_STOP_JOURNAL_SHA256,
                "stop_log_sha256": STOP_LOG_SHA256,
                "pair96_receipt_sha256": PAIR96_SHA256,
                "prior_rate_clearance_sha256": PRIOR_RATE_CLEARANCE_SHA256,
                "last_failed_cell_id": FAILED_CELL_ID,
                "last_failed_run_id": FAILED_RUN_ID,
                "resume_at_order": 193,
                "provider_health_epoch_start_order": 189,
                "new_provider_health_epoch": False,
                "selected_endpoint_recheck": selected,
            },
        )
    finally:
        journal.close()
    return await collect(
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
        provider_pause_clearance=clearance_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--metadata-approval", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        resume(
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.metadata_approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
