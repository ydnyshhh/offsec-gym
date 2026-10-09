"""Reconcile pair 94 and resume only after a separate rate-pause approval.

The three provider-rate-limited cells remain in their frozen positions. This
operational wrapper can open one new provider-health epoch at order 189; it
does not retry a model request or modify the frozen provider-health policy.
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
    EXPECTED_RATE_PAUSE_APPROVAL_SHA256,
    RATE_PAUSE_CLEARANCE_NAME,
    RATE_PAUSE_STOP_JOURNAL_SHA256,
    ComposeRangeRuntime,
    Journal,
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
from m66_resume_after_stage_bound import EXPECTED_STAGE_APPROVAL_SHA256

from offsecgym.research.m64_execute import _sha256

STOP_COST = Decimal("82.502808")
FAILED_CELL_IDS = (
    "4303c4d43cf41308",
    "66d39cb287dad48f",
    "9e5ec582428919e4",
)
FAILED_RUN_IDS = (
    "ace7c439-2e4b-4730-a3d3-f2303ac0c2ec",
    "55eba3ca-517f-4dbf-963e-6866445217a1",
    "188753d3-8485-44ae-aca1-d0d9a40e1c56",
)
RETAINED_INVALID_ORDERS = [89, 90, 170, 186, 187, 188]
PRIOR_STAGE_AMENDMENT = "stage-bound-reconciliation-cell-162.json"


def _require_rate_approval(path: Path) -> str:
    """Fail closed until the exact newly approved receipt hash is pinned."""
    if EXPECTED_RATE_PAUSE_APPROVAL_SHA256 == "0" * 64 or not path.is_file():
        raise ValueError("rate-pause paid continuation lacks separate exact approval")
    raw = path.read_bytes()
    digest = _sha256(raw)
    if digest != EXPECTED_RATE_PAUSE_APPROVAL_SHA256:
        raise ValueError("rate-pause approval artifact hash differs")
    approval = json.loads(raw)
    if (
        approval.get("protocol") != "m66-confirmatory-v2-rate-pause-clearance-v1"
        or approval.get("approved_for_paid_calls") is not True
        or approval.get("approval_scope")
        != "assigned_cells_189_through_280_only_no_retry_or_replacement"
        or approval.get("original_manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or approval.get("original_approval_sha256") != EXPECTED_APPROVAL_SHA256
        or approval.get("prior_pause_approval_sha256") != EXPECTED_PAUSE_APPROVAL_SHA256
        or approval.get("prior_stage_approval_sha256") != EXPECTED_STAGE_APPROVAL_SHA256
        or approval.get("stop_journal_sha256") != RATE_PAUSE_STOP_JOURNAL_SHA256
        or Decimal(str(approval.get("cost_ceiling_usd"))) != Decimal("504")
        or approval.get("provider_health_reset_after_order") != 188
        or approval.get("retained_invalid_cell_orders") != RETAINED_INVALID_ORDERS
        or approval.get("retained_agent_failed_cell_order") != 162
    ):
        raise ValueError("rate-pause approval does not cover this exact stop")
    return digest


def _require_stop(manifest: dict, journal_path: Path) -> tuple[list[dict], Decimal]:
    raw = journal_path.read_bytes()
    if _sha256(raw) != RATE_PAUSE_STOP_JOURNAL_SHA256:
        raise ValueError("rate-pause journal differs from the audited stop")
    records = _journal_records(manifest, raw)
    cost = sum((Decimal(str(record["estimated_cost_usd"])) for record in records), Decimal(0))
    if (
        len(records) != 188
        or cost != STOP_COST
        or tuple(record["cell_id"] for record in records[185:188]) != FAILED_CELL_IDS
        or tuple(record["run_id"] for record in records[185:188]) != FAILED_RUN_IDS
        or any(
            record["status"] != "provider_failed"
            or record["failure_reason"] != "provider_rate_limited"
            or record["score_valid"] is not False
            for record in records[185:188]
        )
        or (journal_path.parent / "pair-postcheck-94.json").exists()
        or (journal_path.parent / RATE_PAUSE_CLEARANCE_NAME).exists()
    ):
        raise ValueError("rate-pause checkpoint has extra work or changed failures")
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
        raise ValueError("pair-94 closure requires both authoritative audits")
    return {
        "protocol": EXPECTED_PROTOCOL,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "pair_number": 94,
        "cell_ids": list(FAILED_CELL_IDS[1:]),
        "run_ids": list(FAILED_RUN_IDS[1:]),
        "audits": audits,
        "endpoint_preflight_sha256": pair_preflight_sha256,
        "cumulative_estimated_cost_usd": str(cumulative_cost),
        "retrospective_rate_pause_closure": True,
    }


async def resume(
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    rate_approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, approval_path
    )
    rate_approval_sha = _require_rate_approval(rate_approval_path)
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("approved provider key and dedicated database URL are required")
    records, cost = _require_stop(manifest, journal_path)
    if cost >= Decimal(str(approval["cost_ceiling_usd"])):
        raise ValueError("stopped sample has reached the approved cost cap")
    if (
        _sha256((journal_path.parent / "pause-approval.json").read_bytes())
        != EXPECTED_PAUSE_APPROVAL_SHA256
        or _sha256((journal_path.parent / "stage-approval.json").read_bytes())
        != EXPECTED_STAGE_APPROVAL_SHA256
        or not (journal_path.parent / "provider-pause-clearance-after-cell-90.json").is_file()
        or not (journal_path.parent / PRIOR_STAGE_AMENDMENT).is_file()
        or not _pair_receipt_path(journal_path, 45).is_file()
        or not _pair_receipt_path(journal_path, 81).is_file()
    ):
        raise ValueError("prior approved operational amendments are absent")
    health = _health_module(protocol_root)
    history = tuple(
        health.CellTerminal(record["status"], record.get("failure_reason")) for record in records
    )
    if health.provider_health(history) != "pause":
        raise ValueError("frozen provider-health gate does not identify this pause")
    first, second = records[186:188]
    if any(first[key] != second[key] for key in ("build_id", "pair_id", "fixture_digest")):
        raise ValueError("pair 94 does not share a frozen build, pair, and fixture")
    preflight_path = journal_path.parent / "endpoint-preflight-94.json"
    first_recheck_path = journal_path.parent / "endpoint-recheck-cell-187.json"
    preflight = json.loads(preflight_path.read_bytes())
    first_recheck = json.loads(first_recheck_path.read_bytes())
    if not _matching_endpoint(preflight, first_recheck):
        raise ValueError("pair-94 endpoint recheck differs from its original route")

    endpoint = manifest["expected_selected_endpoint"]
    journal = Journal(
        journal_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=(endpoint["revision"], endpoint["upstream_provider"]),
        max_estimated_usd=float(approval["cost_ceiling_usd"]),
    )
    try:
        if _sha256(journal_path.read_bytes()) != RATE_PAUSE_STOP_JOURNAL_SHA256:
            raise ValueError("journal changed after exclusive rate-pause lock")
        engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
        events = PostgresEventStore(engine)
        state = ComposeRangeRuntime(state_dir).state
        try:
            audits = []
            for cell, record in zip(manifest["cells"][:188], records, strict=True):
                stage_path = journal_path.parent / "stages" / f"{cell['cell_id']}.json"
                if not stage_path.is_file():
                    raise ValueError("stopped cell lacks its pinned stage output")
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
                for cell, record in zip(manifest["cells"][:188], records, strict=True)
            }
            await audit_pair_inventory(engine, events, completed, audits)
        finally:
            await engine.dispose()

        # The live public route check precedes both create-only receipts.
        current_endpoint = _fresh_endpoint_check(manifest)
        if not _matching_endpoint(preflight, current_endpoint):
            raise ValueError("selected endpoint or price differs after rate pause")
        if _sha256(journal_path.read_bytes()) != RATE_PAUSE_STOP_JOURNAL_SHA256:
            raise ValueError("journal changed during authoritative rate-pause reconciliation")
        pair_receipt_sha = _write_json_create_only(
            _pair_receipt_path(journal_path, 94),
            _retrospective_pair_receipt(
                audits=audits[186:188],
                pair_preflight_sha256=_sha256(preflight_path.read_bytes()),
                cumulative_cost=cost,
            ),
        )
        clearance_path = journal_path.parent / RATE_PAUSE_CLEARANCE_NAME
        _write_json_create_only(
            clearance_path,
            {
                "protocol": "m66-confirmatory-v2-rate-pause-clearance-v1",
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "approval_sha256": _sha256(approval_path.read_bytes()),
                "rate_pause_approval_sha256": rate_approval_sha,
                "stop_journal_sha256": RATE_PAUSE_STOP_JOURNAL_SHA256,
                "pair94_receipt_sha256": pair_receipt_sha,
                "provider_health_before": "pause",
                "provider_health_epoch_start_order": 189,
                "failed_cell_ids": list(FAILED_CELL_IDS),
                "failed_run_ids": list(FAILED_RUN_IDS),
                "selected_endpoint_recheck": current_endpoint,
            },
        )
        checked = await postcheck(
            source_root, protocol_root, manifest_path, approval_path, journal_path, state_dir
        )
        if checked["journaled_cells"] != 188:
            raise ValueError("rate-pause closure changed the frozen run inventory")
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
    parser.add_argument("--rate-approval", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        resume(
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.rate_approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
