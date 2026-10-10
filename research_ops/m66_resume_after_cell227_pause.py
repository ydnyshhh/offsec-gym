"""Reconcile the exact cell-227 provider pause before any approved new call.

Cells 226 and 227 remain failed. Only the frozen, unstarted partner cell 228
may complete pair 114 before the unchanged collector receives cells 229–280.
The approval pin is zero until the separate exact-scope decision is bound.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

from m66_confirmatory_audit import audit_cell, audit_pair_inventory
from m66_confirmatory_collect import (
    CELL227_PAUSE_CLEARANCE_NAME,
    CELL227_PAUSE_STOP_JOURNAL_SHA256,
    CELL_WORST_USD,
    EXPECTED_APPROVAL_SHA256,
    EXPECTED_CELL227_PAUSE_APPROVAL_SHA256,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256,
    EXPECTED_PROTOCOL,
    PRIOR_CELL208_APPROVAL_SHA256,
    BootstrappedMonolithicExperimentRunner,
    ComposeRangeRuntime,
    EndpointGuard,
    Journal,
    PostgresEventStore,
    WitnessPlanningExperimentRunner,
    _approval_and_manifest,
    _fresh_endpoint_check,
    _health_module,
    _pair_receipt_path,
    _record,
    _require_clean_checkout,
    _spec,
    _utc_now,
    _write_json_create_only,
    close_completed_monolithic_coverage,
    collect,
    create_async_engine,
)
from m66_confirmatory_postcheck import audit as postcheck

from offsecgym.research.m64_execute import _sha256

PROTOCOL = "m66-confirmatory-v2-cell227-provider-pause-v1"
EXECUTION_COMMIT = "8f77599c0818081313635f40788a6e6e4ed45348"
STOP_LOG_SHA256 = "760b0256880b2c70dfe6bd986f132581038fe9c0b53c0b6a75cb05b6999eeab9"
STOP_COST = Decimal("99.426024")
PAIR_113_SHA256 = "bab524b3d2581ebe2ecf66d3c809032a2576bc3edfe3224446aa585e7d0c40de"
ENDPOINT_114_SHA256 = "44822e1f352b368e9c48cd61ad8eeb5a88a234df30cb1050fcf2a0ce40b47f22"
CELL208_AMENDMENT_SHA256 = "aefeb123d53c1e3dfbfdde68ac21f01503f69c124ce3e6fd05889bf55cc54021"
FAILED_CELL_IDS = ["abab5c763a28da9e", "9a210528132507a9"]
FAILED_RUN_IDS = [
    "93835eb3-a21a-4ec1-b115-8dec634623b7",
    "5f6e678e-c0d0-4492-8162-f3dadd42f1d2",
]
RETAINED_INVALID_ORDERS = [89, 90, 170, 186, 187, 188, 192, 226, 227]


def _require_frozen_execution_files(execution_root: Path) -> None:
    _require_clean_checkout(execution_root)
    for name in ("m66_confirmatory_audit.py", "m66_confirmatory_postcheck.py"):
        path = f"research_ops/{name}"
        original = subprocess.check_output(
            ["git", "show", f"{EXECUTION_COMMIT}:{path}"], cwd=execution_root
        )
        if (execution_root / path).read_bytes() != original:
            raise ValueError(f"frozen audit file changed: {path}")


def _require_approval(path: Path) -> str:
    if EXPECTED_CELL227_PAUSE_APPROVAL_SHA256 == "0" * 64 or not path.is_file():
        raise ValueError("cell-227 pause lacks separate exact paid-call approval")
    digest = _sha256(path.read_bytes())
    if digest != EXPECTED_CELL227_PAUSE_APPROVAL_SHA256:
        raise ValueError("cell-227 pause approval artifact hash differs")
    value = json.loads(path.read_bytes())
    if (
        value.get("protocol") != PROTOCOL
        or value.get("approved_for_paid_calls") is not True
        or value.get("approval_scope")
        != "assigned_cells_228_through_280_only_no_retry_or_replacement"
        or value.get("original_manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or value.get("original_approval_sha256") != EXPECTED_APPROVAL_SHA256
        or value.get("prior_metadata_approval_sha256") != EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256
        or value.get("prior_cell208_approval_sha256") != PRIOR_CELL208_APPROVAL_SHA256
        or value.get("stop_journal_sha256") != CELL227_PAUSE_STOP_JOURNAL_SHA256
        or value.get("provider_health_epoch_start_order") != 228
        or value.get("new_provider_health_epoch") is not True
        or value.get("retained_invalid_cell_orders") != RETAINED_INVALID_ORDERS
        or value.get("retained_agent_failed_cell_order") != 162
        or value.get("retained_score_valid_cell_order") != 208
        or Decimal(str(value.get("cost_ceiling_usd"))) != Decimal("504")
    ):
        raise ValueError("cell-227 approval does not cover this exact stop")
    return digest


def _require_stop(manifest: dict, journal_path: Path, result: dict) -> tuple[dict, dict]:
    base = journal_path.parent
    if (
        _sha256(journal_path.read_bytes()) != CELL227_PAUSE_STOP_JOURNAL_SHA256
        or result["journal_sha256"] != CELL227_PAUSE_STOP_JOURNAL_SHA256
        or result["manifest_sha256"] != EXPECTED_MANIFEST_SHA256
        or result["journaled_cells"] != 227
        or result["score_valid_cells"] != 218
        or result["provider_failed_cells"] != 9
        or Decimal(result["estimated_model_token_cost_usd"]) != STOP_COST
        or _sha256((base / "collector-cell208-reconciled.log").read_bytes()) != STOP_LOG_SHA256
        or _sha256(_pair_receipt_path(journal_path, 113).read_bytes()) != PAIR_113_SHA256
        or _sha256((base / "endpoint-preflight-114.json").read_bytes()) != ENDPOINT_114_SHA256
        or _sha256((base / "cell208-redaction-reconciliation-v1.json").read_bytes())
        != CELL208_AMENDMENT_SHA256
        or _pair_receipt_path(journal_path, 114).exists()
        or (base / "endpoint-recheck-cell-227.json").exists()
        or (base / CELL227_PAUSE_CLEARANCE_NAME).exists()
        or (base / "traces" / f"{manifest['cells'][227]['cell_id']}.json").exists()
        or (base / "stages" / f"{manifest['cells'][227]['cell_id']}.json").exists()
    ):
        raise ValueError("cell-227 stop differs from authoritative reviewed checkpoint")
    first, second = manifest["cells"][226:228]
    stopped_records = [
        row
        for row in (json.loads(line) for line in journal_path.read_bytes().splitlines())
        if row.get("type") == "cell_completed"
    ][-2:]
    if (
        len(stopped_records) != 2
        or [row["cell_id"] for row in stopped_records] != FAILED_CELL_IDS
        or [row["run_id"] for row in stopped_records] != FAILED_RUN_IDS
        or any(
            row["status"] != "provider_failed"
            or row["failure_reason"] != "provider_unavailable"
            or row["score_valid"] is not False
            for row in stopped_records
        )
        or first["cell_id"] != FAILED_CELL_IDS[1]
        or second["cell_id"] in result["cells"]
    ):
        raise ValueError("cell-227 partner or retained failure differs")
    return first, second


def _matching_endpoint(original: dict, current: dict) -> bool:
    return all(
        original.get(field) == current.get(field)
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
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    original_approval_path: Path,
    pause_approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    _require_frozen_execution_files(execution_root)
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, original_approval_path
    )
    pause_approval_sha = _require_approval(pause_approval_path)
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("provider key and dedicated database URL are required")
    result = await postcheck(
        source_root, protocol_root, manifest_path, original_approval_path, journal_path, state_dir
    )
    first, second = _require_stop(manifest, journal_path, result)
    base = journal_path.parent
    if pause_approval_path != (base / "cell227-approval.json").resolve():
        raise ValueError("cell-227 approval path differs from the sealed study directory")
    if _sha256((base / "cell208-approval.json").read_bytes()) != PRIOR_CELL208_APPROVAL_SHA256:
        raise ValueError("prior cell-208 approval differs")
    health = _health_module(protocol_root)
    completed_records = [
        row
        for row in (json.loads(line) for line in journal_path.read_bytes().splitlines())
        if row.get("type") == "cell_completed"
    ]
    history = tuple(
        health.CellTerminal(record["status"], record.get("failure_reason"))
        for record in completed_records
    )
    if len(history) != 227 or health.provider_health(history[188:]) != "pause":
        raise ValueError("frozen provider-health policy did not pause at cell 227")
    spent = Decimal(result["estimated_model_token_cost_usd"])
    cap = Decimal(str(approval["cost_ceiling_usd"]))
    if spent + CELL_WORST_USD > cap:
        raise ValueError("insufficient approved cumulative capacity for cell 228")
    spec = _spec(protocol_root, manifest, second)
    if spec != _spec(protocol_root, manifest, first):
        raise ValueError("partial pair arms differ in their frozen experiment spec")

    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    runtime = ComposeRangeRuntime(state_dir)
    endpoint = manifest["expected_selected_endpoint"]
    guard = EndpointGuard((endpoint["revision"], endpoint["upstream_provider"]))
    journal = Journal(
        journal_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=guard.expected,
        max_estimated_usd=float(cap),
    )
    clearance_path = base / CELL227_PAUSE_CLEARANCE_NAME
    try:
        if _sha256(journal_path.read_bytes()) != CELL227_PAUSE_STOP_JOURNAL_SHA256:
            raise ValueError("journal changed after exclusive cell-227 lock")
        selected = _fresh_endpoint_check(manifest)
        original_endpoint = json.loads((base / "endpoint-preflight-114.json").read_bytes())
        if not _matching_endpoint(original_endpoint, selected):
            raise ValueError("selected endpoint or price differs from pair-114 preflight")
        if _sha256(journal_path.read_bytes()) != CELL227_PAUSE_STOP_JOURNAL_SHA256:
            raise ValueError("journal changed during cell-227 route replay")
        recheck_sha = _write_json_create_only(base / "endpoint-recheck-cell-227.json", selected)
        _write_json_create_only(
            clearance_path,
            {
                "protocol": PROTOCOL,
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "original_approval_sha256": _sha256(original_approval_path.read_bytes()),
                "cell227_approval_sha256": pause_approval_sha,
                "stop_journal_sha256": CELL227_PAUSE_STOP_JOURNAL_SHA256,
                "stop_log_sha256": STOP_LOG_SHA256,
                "pair113_receipt_sha256": PAIR_113_SHA256,
                "endpoint_preflight_114_sha256": ENDPOINT_114_SHA256,
                "endpoint_recheck_cell227_sha256": recheck_sha,
                "failed_cell_ids": FAILED_CELL_IDS,
                "failed_run_ids": FAILED_RUN_IDS,
                "provider_health_before": "pause",
                "provider_health_epoch_start_order": 228,
                "new_provider_health_epoch": True,
                "selected_endpoint_recheck": selected,
            },
        )
        journal.append(
            {"type": "cell_started", "cell_id": second["cell_id"], "order": 228, "at": _utc_now()}
        )
        runner_type = (
            BootstrappedMonolithicExperimentRunner
            if second["arm"] == "control"
            else WitnessPlanningExperimentRunner
        )
        outcome = await runner_type(
            runtime, events, guard.provider(os.environ["OPENROUTER_API_KEY"])
        ).run(spec)
        if outcome.build_id is None:
            raise ValueError("cell 228 lacks a build; reconcile its started event")
        build = runtime.state.verify_build_integrity(outcome.build_id)
        if build.pair_id is None or build.spec != spec.range:
            raise ValueError("cell 228 build or fixture binding differs")
        await close_completed_monolithic_coverage(events, outcome.run_id)
        trace = await events.read_run(outcome.run_id)
        trace_path = base / "traces" / f"{second['cell_id']}.json"
        if trace_path.exists():
            raise ValueError("cell 228 trace exists before its one permitted run")
        record = _record(
            cell=second,
            order=228,
            outcome=outcome,
            spec=spec,
            trace=trace,
            build=build,
            trace_path=trace_path,
        )
        journal.append(record)
        journal.completed[second["cell_id"]] = record
        spent += Decimal(str(record["estimated_cost_usd"]))
        prior = next(
            row
            for row in (json.loads(line) for line in journal_path.read_bytes().splitlines())
            if row.get("type") == "cell_completed" and row["cell_id"] == first["cell_id"]
        )
        if (
            (record["build_id"], record["pair_id"], record["fixture_digest"])
            != (prior["build_id"], prior["pair_id"], prior["fixture_digest"])
            or guard.stopped
            or spent > cap
        ):
            raise ValueError("cell 228 differs in pair, endpoint, or approved cost")
        partner_audit = await audit_cell(
            events=events,
            state=runtime.state,
            manifest=manifest,
            manifest_path=manifest_path,
            protocol_root=protocol_root,
            state_dir=state_dir,
            journal_dir=base,
            cell=second,
            record=record,
            spec=spec,
        )
        audits = [result["cells"][cell["cell_id"]] for cell in manifest["cells"][:227]]
        audits.append(partner_audit)
        await audit_pair_inventory(engine, events, journal.completed, audits)
        if (
            health.provider_health(
                (health.CellTerminal(record["status"], record.get("failure_reason")),)
            )
            != "continue"
        ):
            raise ValueError("new provider-health epoch stopped after cell 228")
        _write_json_create_only(
            _pair_receipt_path(journal_path, 114),
            {
                "protocol": EXPECTED_PROTOCOL,
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "pair_number": 114,
                "cell_ids": [first["cell_id"], second["cell_id"]],
                "run_ids": [prior["run_id"], record["run_id"]],
                "audits": [result["cells"][first["cell_id"]], partner_audit],
                "endpoint_preflight_sha256": ENDPOINT_114_SHA256,
                "cumulative_estimated_cost_usd": str(spent),
            },
        )
    finally:
        journal.close()
        await engine.dispose()

    checked = await postcheck(
        source_root, protocol_root, manifest_path, original_approval_path, journal_path, state_dir
    )
    if checked["journaled_cells"] != 228:
        raise ValueError("cell-228 authoritative full postcheck did not close pair 114")
    return await collect(
        source_root,
        protocol_root,
        manifest_path,
        original_approval_path,
        journal_path,
        state_dir,
        provider_pause_clearance=clearance_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--cell227-approval", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        resume(
            args.execution_root.resolve(),
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.cell227_approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
