"""Read-only replay of the retained first failure and any approved M6.6.1 continuation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

from m661_cell1_recovery import (
    EXPECTED_CONTINUATION_APPROVAL_SHA256,
    STOP_JOURNAL_SHA256,
    STOP_RUN_ID,
    reconcile,
)
from m661_feasibility_collect import (
    CAP,
    EXPECTED_MANIFEST_SHA256,
    REVISION,
    UPSTREAM,
    _approval_and_manifest,
    _audit_cell,
    _health_decision,
    _load_module,
    _sha256,
)
from m661_resume_after_cell1_permission import _inventory_with_retained_first, _scope_approval
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.storage.event_store import PostgresEventStore


def _continuation_records(manifest: dict, path: Path) -> list[dict]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("continuation journal is absent or unsafe")
    lines = [json.loads(line) for line in path.read_bytes().splitlines()]
    if not lines or any(
        lines[0].get(key) != value
        for key, value in (
            ("type", "batch_started"),
            ("manifest_sha256", EXPECTED_MANIFEST_SHA256),
            ("expected_model_revision", REVISION),
            ("expected_upstream_provider", UPSTREAM),
            ("max_estimated_usd", float(CAP)),
        )
    ):
        raise ValueError("continuation journal header differs")
    if (len(lines) - 1) % 2:
        raise ValueError("interrupted continuation cell requires separate reconciliation")
    records: list[dict] = []
    for index, (started, record) in enumerate(zip(lines[1::2], lines[2::2], strict=True)):
        if index + 1 >= len(manifest["cells"]):
            raise ValueError("continuation exceeds frozen assignments")
        cell = manifest["cells"][index + 1]
        if any(
            (
                started.get("type") != "cell_started",
                record.get("type") != "cell_completed",
                started.get("cell_id") != cell["cell_id"],
                record.get("cell_id") != cell["cell_id"],
                started.get("order") != cell["order"],
                record.get("order") != cell["order"],
                started.get("run_id") != record.get("run_id"),
                record.get("experiment_sha256") != cell["experiment_sha256"],
                record.get("run_id") == str(STOP_RUN_ID),
            )
        ):
            raise ValueError("continuation journal differs from frozen assigned order")
        records.append(record)
    if len({record["run_id"] for record in records}) != len(records):
        raise ValueError("continuation journal repeats a run")
    return records


def _first_missing_row(analysis, cell: dict, audit: dict) -> dict:
    row = analysis.missing_checkpoint_cell(
        {**cell, "model_run_id": audit["run_id"]},
        terminal_status="environment_failed",
        reason="infrastructure_permission_failure_before_checkpoint",
    )
    # Independent authoritative event replay proves zero usage even though the
    # checkpoint outcome is missing.  HTTP zero follows from the two-event trace.
    row.update(
        {
            "resource_auditable": True,
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "http_actions": 0,
            "trusted_setup_http_actions": 0,
            "resource_recovered_from_authoritative_events": True,
        }
    )
    return row


async def postcheck(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
    *,
    require_complete: bool,
) -> dict:
    manifest, _ = _approval_and_manifest(
        execution_root, source_root, protocol_root, manifest_path, approval_path
    )
    first_audit = await reconcile(
        execution_root,
        source_root,
        protocol_root,
        manifest_path,
        approval_path,
        journal_path,
        state_dir,
        allow_continuation=True,
    )
    private_dir = journal_path.parent
    if (private_dir / "cell1-continuation-approval.json").parent != approval_path.parent:
        raise ValueError("continuation approval directory differs")
    _scope_approval(private_dir / "cell1-continuation-approval.json", manifest)
    continuation_path = private_dir / "continuation-journal.jsonl"
    recheck_raw = (private_dir / "endpoint-recheck-cell-1.json").read_bytes()
    clearance = json.loads((private_dir / "cell1-infrastructure-clearance.json").read_bytes())
    if (
        clearance.get("protocol") != manifest["protocol"]
        or clearance.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or clearance.get("original_journal_sha256") != STOP_JOURNAL_SHA256
        or clearance.get("retained_first_cell") != first_audit
        or clearance.get("continuation_approval_sha256") != EXPECTED_CONTINUATION_APPROVAL_SHA256
        or clearance.get("endpoint_recheck_sha256") != _sha256(recheck_raw)
        or clearance.get("next_cell_order") != 2
        or clearance.get("permission_qualification")
        != "fresh no-model range healthy with umask 022"
    ):
        raise ValueError("cell-1 infrastructure clearance differs")
    records = _continuation_records(manifest, continuation_path)
    if require_complete and len(records) != 95:
        raise ValueError("feasibility sample did not execute all remaining assignments")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("dedicated database URL is required")
    engine = create_async_engine(database_url)
    events = PostgresEventStore(engine)
    state = ComposeRangeRuntime(state_dir).state
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_recovery_postcheck_analysis",
    )
    try:
        audits: list[dict] = []
        rows: list[dict] = [_first_missing_row(analysis, manifest["cells"][0], first_audit)]
        cumulative = Decimal(0)
        conservative = Decimal(0)
        for index, record in enumerate(records):
            cell = manifest["cells"][index + 1]
            audit = await _audit_cell(events, state, analysis, cell, record, None)
            audits.append(audit)
            rows.append(json.loads(Path(record["stage_path"]).read_bytes()))
            cost = Decimal(str(record["estimated_cost_usd"]))
            cumulative += cost
            conservative += cost if record["resource_auditable"] else Decimal("1.80")
            if cumulative > CAP or conservative > CAP:
                raise ValueError("approved cumulative cost cap exceeded")
            if _health_decision(records[: index + 1]) != "continue" and index + 1 < len(records):
                raise ValueError("collector continued after provider-health stop")
        await _inventory_with_retained_first(engine, events, records, audits)
        pair_count = (len(records) + 1) // 2
        for pair_number in range(1, pair_count + 1):
            pair = manifest["cells"][2 * pair_number - 2 : 2 * pair_number]
            paired_records = records[2 * pair_number - 3 : 2 * pair_number - 1]
            paired_audits = audits[2 * pair_number - 3 : 2 * pair_number - 1]
            if pair_number == 1:
                paired_records = records[:1]
                paired_audits = [first_audit, *audits[:1]]
                run_ids = [str(STOP_RUN_ID), records[0]["run_id"]]
                endpoint_path = private_dir / "endpoint-recheck-cell-1.json"
            else:
                run_ids = [record["run_id"] for record in paired_records]
                endpoint_path = private_dir / f"endpoint-preflight-{pair_number}.json"
            receipt = json.loads((private_dir / f"pair-postcheck-{pair_number}.json").read_bytes())
            endpoint_raw = endpoint_path.read_bytes()
            endpoint = json.loads(endpoint_raw)
            if (
                receipt.get("protocol") != manifest["protocol"]
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("pair_number") != pair_number
                or receipt.get("cell_ids") != [cell["cell_id"] for cell in pair]
                or receipt.get("run_ids") != run_ids
                or receipt.get("audits") != paired_audits
                or receipt.get("endpoint_preflight_sha256") != _sha256(endpoint_raw)
                or receipt.get("original_pair1_preflight_sha256")
                != (first_audit["endpoint_preflight_sha256"] if pair_number == 1 else None)
                or endpoint.get("endpoint") != f"{UPSTREAM} | {REVISION}"
                or endpoint.get("input_usd_per_million_tokens") != 3.0
                or endpoint.get("output_usd_per_million_tokens") != 15.0
            ):
                raise ValueError("pair or selected endpoint receipt differs")
            pair_cost = sum(
                (
                    Decimal(str(item["estimated_cost_usd"]))
                    for item in records[: 2 * pair_number - 1]
                ),
                Decimal(0),
            )
            if Decimal(receipt["cumulative_estimated_cost_usd"]) != pair_cost:
                raise ValueError("pair cumulative estimated cost differs")
            pair_conservative = sum(
                (
                    Decimal(str(item["estimated_cost_usd"]))
                    if item["resource_auditable"]
                    else Decimal("1.80")
                    for item in records[: 2 * pair_number - 1]
                ),
                Decimal(0),
            )
            if Decimal(receipt["conservative_cost_usd"]) != pair_conservative:
                raise ValueError("pair conservative cost differs")
        if len(list(private_dir.glob("pair-postcheck-*.json"))) != pair_count:
            raise ValueError("extra or missing pair receipt")
        expected_endpoint_count = pair_count + (len(records) + 1) % 2
        if len(list(private_dir.glob("endpoint-preflight-*.json"))) != expected_endpoint_count:
            raise ValueError("extra or missing endpoint preflight")
        if len(list(private_dir.glob("endpoint-recheck-cell-*.json"))) != 1:
            raise ValueError("extra or missing recovery endpoint recheck")
        if len(list((private_dir / "stages").glob("*.json"))) != len(records):
            raise ValueError("extra or missing checkpoint stage")
        if len(list((private_dir / "traces").glob("*.json"))) != len(records):
            raise ValueError("extra or missing saved source trace")
        for cell in manifest["cells"][len(records) + 1 :]:
            rows.append(
                analysis.missing_checkpoint_cell(
                    cell, terminal_status="unstarted", reason="no_model_run"
                )
            )
        curves = analysis.checkpoint_response_curves(
            rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
        )
        paired = analysis.paired_checkpoint_differences(
            rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
        )
        analysis_raw = (
            json.dumps({"rows": rows, "curves": curves, "paired": paired}, sort_keys=True, indent=2)
            + "\n"
        ).encode()
        return {
            "protocol": manifest["protocol"],
            "assigned_cells": 96,
            "retained_infrastructure_failure_cells": 1,
            "continued_cells": len(records),
            "completed_pairs": pair_count,
            "original_journal_sha256": STOP_JOURNAL_SHA256,
            "continuation_journal_sha256": _sha256(continuation_path.read_bytes()),
            "estimated_cost_usd": str(cumulative),
            "conservative_cost_usd": str(conservative),
            "score_valid_cells": sum(record["score_valid"] for record in records),
            "provider_failed_cells": sum(
                record["status"] == "provider_failed" for record in records
            ),
            "analysis_sha256": _sha256(analysis_raw),
        }
    finally:
        await engine.dispose()


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
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        postcheck(
            args.execution_root,
            args.source_root,
            args.protocol_root,
            args.manifest,
            args.approval,
            args.journal,
            args.state_dir,
            require_complete=args.require_complete,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
