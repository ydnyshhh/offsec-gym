"""Reconcile pair 45 and resume the approved v2 assignment after a health pause.

Both provider-failed cells stay in the frozen sample. This operational amendment
starts a new provider-health epoch at order 91; it never retries either cell.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

from m66_confirmatory_audit import audit_cell, audit_pair_inventory
from m66_confirmatory_collect import (
    EXPECTED_APPROVAL_SHA256,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_PAUSE_APPROVAL_SHA256,
    EXPECTED_PROTOCOL,
    PROVIDER_PAUSE_STOP_JOURNAL_SHA256,
    ComposeRangeRuntime,
    PostgresEventStore,
    _approval_and_manifest,
    _fresh_endpoint_check,
    _health_module,
    _pair_receipt_path,
    _spec,
    _write_json_create_only,
    collect,
    create_async_engine,
)
from m66_confirmatory_postcheck import _journal_records
from m66_confirmatory_postcheck import audit as postcheck

from offsecgym.research.m64_execute import _sha256

STOP_JOURNAL_SHA256 = PROVIDER_PAUSE_STOP_JOURNAL_SHA256
STOP_COST = Decimal("39.286650")
FAILED_CELL_IDS = ("d22b46a80a5008c9", "278c79865b5f3895")
FAILED_RUN_IDS = (
    "ce9a6401-17d0-49ed-a120-187b4927b6b7",
    "4ea5886d-ebcb-41b5-8595-d00a7eb13a3c",
)
CLEARANCE_NAME = "provider-pause-clearance-after-cell-90.json"


def _require_pause_approval(path: Path) -> str:
    """Stay closed until a separately authorized receipt hash is pinned."""
    if EXPECTED_PAUSE_APPROVAL_SHA256 == "0" * 64 or not path.is_file():
        raise ValueError("provider-pause paid continuation lacks exact separate approval")
    raw = path.read_bytes()
    digest = _sha256(raw)
    if digest != EXPECTED_PAUSE_APPROVAL_SHA256:
        raise ValueError("provider-pause approval artifact hash differs")
    approval = json.loads(raw)
    if (
        approval.get("protocol") != "m66-confirmatory-v2-provider-pause-clearance-v1"
        or approval.get("approved_for_paid_calls") is not True
        or approval.get("approval_scope")
        != "assigned_cells_91_through_280_only_no_retry_or_replacement"
        or approval.get("original_manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("original_approval_sha256") != EXPECTED_APPROVAL_SHA256
        or approval.get("stop_journal_sha256") != STOP_JOURNAL_SHA256
        or Decimal(str(approval.get("cost_ceiling_usd"))) != Decimal("504")
        or approval.get("provider_health_reset_after_order") != 90
        or approval.get("retained_invalid_cell_orders") != [89, 90]
    ):
        raise ValueError("provider-pause approval does not cover this exact stop")
    return digest


def _require_stop(manifest: dict, journal_path: Path) -> tuple[list[dict], Decimal]:
    raw = journal_path.read_bytes()
    if _sha256(raw) != STOP_JOURNAL_SHA256:
        raise ValueError("provider-pause journal differs from the audited stop")
    records = _journal_records(manifest, raw)
    cost = sum((Decimal(str(record["estimated_cost_usd"])) for record in records), Decimal(0))
    if (
        len(records) != 90
        or cost != STOP_COST
        or tuple(record["cell_id"] for record in records[88:90]) != FAILED_CELL_IDS
        or tuple(record["run_id"] for record in records[88:90]) != FAILED_RUN_IDS
        or any(
            record["status"] != "provider_failed"
            or record["failure_reason"] != "provider_unavailable"
            or record["score_valid"] is not False
            for record in records[88:90]
        )
        or (journal_path.parent / "pair-postcheck-45.json").exists()
        or (journal_path.parent / CLEARANCE_NAME).exists()
    ):
        raise ValueError("provider-pause checkpoint has extra work or a changed failure")
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


def _retrospective_pair_receipt(
    *, audits: list[dict], pair_preflight_sha256: str, cumulative_cost: Decimal
) -> dict:
    if len(audits) != 2:
        raise ValueError("retrospective pair receipt requires both authoritative audits")
    return {
        "protocol": EXPECTED_PROTOCOL,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "pair_number": 45,
        "cell_ids": list(FAILED_CELL_IDS),
        "run_ids": list(FAILED_RUN_IDS),
        "audits": audits,
        "endpoint_preflight_sha256": pair_preflight_sha256,
        "cumulative_estimated_cost_usd": str(cumulative_cost),
        "retrospective_provider_pause_closure": True,
    }


async def resume(
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    pause_approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, approval_path
    )
    pause_approval_sha = _require_pause_approval(pause_approval_path)
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("approved provider key and dedicated database URL are required")
    records, cost = _require_stop(manifest, journal_path)
    if cost > Decimal(str(approval["cost_ceiling_usd"])):
        raise ValueError("stopped sample exceeds the approved estimated-cost cap")
    health = _health_module(protocol_root)
    history = tuple(
        health.CellTerminal(record["status"], record.get("failure_reason")) for record in records
    )
    if health.provider_health(history) != "pause":
        raise ValueError("frozen provider-health gate does not identify this pause")
    first, second = records[88:90]
    if any(first[key] != second[key] for key in ("build_id", "pair_id", "fixture_digest")):
        raise ValueError("the two provider-failed arms do not share a frozen pair")
    pair_preflight_path = journal_path.parent / "endpoint-preflight-45.json"
    first_recheck_path = journal_path.parent / "endpoint-recheck-cell-89.json"
    pair_preflight = json.loads(pair_preflight_path.read_bytes())
    first_recheck = json.loads(first_recheck_path.read_bytes())
    if not _matching_endpoint(pair_preflight, first_recheck):
        raise ValueError("pair-45 endpoint recheck differs from its original route")

    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    state = ComposeRangeRuntime(state_dir).state
    try:
        audits = []
        for cell, record in zip(manifest["cells"][:90], records, strict=True):
            stage_path = journal_path.parent / "stages" / f"{cell['cell_id']}.json"
            if not stage_path.is_file():
                raise ValueError("stopped cell lacks its frozen stage output")
            audits.append(
                await audit_cell(
                    events=events,
                    state=state,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    protocol_root=protocol_root,
                    state_dir=state_dir,
                    journal_dir=journal_path.parent,
                    cell=cell,
                    record=record,
                    spec=_spec(protocol_root, manifest, cell),
                    expected_stage_sha256=_sha256(stage_path.read_bytes()),
                )
            )
        completed = {
            cell["cell_id"]: record
            for cell, record in zip(manifest["cells"][:90], records, strict=True)
        }
        await audit_pair_inventory(engine, events, completed, audits)
    finally:
        await engine.dispose()

    # A public no-key check is required before either create-only receipt.
    current_endpoint = _fresh_endpoint_check(manifest)
    if not _matching_endpoint(pair_preflight, current_endpoint):
        raise ValueError("selected endpoint or price differs after provider pause")
    if _sha256(journal_path.read_bytes()) != STOP_JOURNAL_SHA256:
        raise ValueError("journal changed during authoritative pause reconciliation")
    pair_receipt_path = _pair_receipt_path(journal_path, 45)
    pair_receipt_sha = _write_json_create_only(
        pair_receipt_path,
        _retrospective_pair_receipt(
            audits=audits[88:90],
            pair_preflight_sha256=_sha256(pair_preflight_path.read_bytes()),
            cumulative_cost=cost,
        ),
    )
    clearance_path = journal_path.parent / CLEARANCE_NAME
    _write_json_create_only(
        clearance_path,
        {
            "protocol": "m66-confirmatory-v2-provider-pause-clearance-v1",
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "approval_sha256": _sha256(approval_path.read_bytes()),
            "pause_approval_sha256": pause_approval_sha,
            "stop_journal_sha256": STOP_JOURNAL_SHA256,
            "pair45_receipt_sha256": pair_receipt_sha,
            "provider_health_before": "pause",
            "provider_health_epoch_start_order": 91,
            "failed_cell_ids": list(FAILED_CELL_IDS),
            "failed_run_ids": list(FAILED_RUN_IDS),
            "selected_endpoint_recheck": current_endpoint,
        },
    )

    # This audit requires the now-complete pair-45 receipt and the original
    # endpoint recheck; no cell 91 can start until the full prefix passes.
    checked = await postcheck(
        source_root, protocol_root, manifest_path, approval_path, journal_path, state_dir
    )
    if checked["journaled_cells"] != 90:
        raise ValueError("provider-pause closure changed the assigned run inventory")
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
    parser.add_argument("--pause-approval", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        resume(
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.pause_approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
