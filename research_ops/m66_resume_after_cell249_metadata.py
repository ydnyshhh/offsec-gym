"""Reconcile the exact cell-249 metadata transport stop before any new call.

Cell 249 remains provider-failed. Only the frozen, unstarted partner cell 250
may close pair 125 before the unchanged collector receives cells 251–280.
The cell-228 provider-health epoch continues without a reset at cell 250.
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
    CELL249_ENDPOINT125_SHA256,
    CELL249_METADATA_CLEARANCE_NAME,
    CELL249_METADATA_STOP_JOURNAL_SHA256,
    CELL249_METADATA_STOP_LOG_SHA256,
    CELL249_PAIR124_SHA256,
    CELL_WORST_USD,
    EXPECTED_APPROVAL_SHA256,
    EXPECTED_CELL227_PAUSE_APPROVAL_SHA256,
    EXPECTED_CELL249_METADATA_APPROVAL_SHA256,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_PROTOCOL,
    PRIOR_CELL227_CLEARANCE_SHA256,
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

PROTOCOL = "m66-confirmatory-v2-cell249-metadata-transport-v1"
EXECUTION_COMMIT = "80a45afc22815e48ceb23b5e7e7ae98e78214c9d"
STOP_COST = Decimal("109.051497")
FAILED_CELL_ID = "6d8efdd22e04cf42"
FAILED_RUN_ID = "0e741b77-20e3-4081-8915-3cfc01a82992"
RETAINED_INVALID_ORDERS = [89, 90, 170, 186, 187, 188, 192, 226, 227, 249]


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
    if EXPECTED_CELL249_METADATA_APPROVAL_SHA256 == "0" * 64 or not path.is_file():
        raise ValueError("cell-249 metadata recovery lacks separate exact paid-call approval")
    digest = _sha256(path.read_bytes())
    if digest != EXPECTED_CELL249_METADATA_APPROVAL_SHA256:
        raise ValueError("cell-249 metadata recovery approval artifact hash differs")
    value = json.loads(path.read_bytes())
    if (
        value.get("protocol") != PROTOCOL
        or value.get("approved_for_paid_calls") is not True
        or value.get("approval_scope")
        != "assigned_cells_250_through_280_only_no_retry_or_replacement"
        or value.get("original_manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or value.get("original_approval_sha256") != EXPECTED_APPROVAL_SHA256
        or value.get("prior_cell227_approval_sha256") != EXPECTED_CELL227_PAUSE_APPROVAL_SHA256
        or value.get("stop_journal_sha256") != CELL249_METADATA_STOP_JOURNAL_SHA256
        or value.get("provider_health_epoch_start_order") != 228
        or value.get("new_provider_health_epoch") is not False
        or value.get("retained_invalid_cell_orders") != RETAINED_INVALID_ORDERS
        or value.get("retained_agent_failed_cell_order") != 162
        or value.get("retained_score_valid_cell_order") != 208
        or Decimal(str(value.get("cost_ceiling_usd"))) != Decimal("504")
    ):
        raise ValueError("cell-249 approval does not cover this exact stop")
    return digest


def _require_stop(manifest: dict, journal_path: Path, result: dict) -> tuple[dict, dict]:
    base = journal_path.parent
    if (
        _sha256(journal_path.read_bytes()) != CELL249_METADATA_STOP_JOURNAL_SHA256
        or result["journal_sha256"] != CELL249_METADATA_STOP_JOURNAL_SHA256
        or result["manifest_sha256"] != EXPECTED_MANIFEST_SHA256
        or result["journaled_cells"] != 249
        or result["score_valid_cells"] != 239
        or result["provider_failed_cells"] != 10
        or Decimal(result["estimated_model_token_cost_usd"]) != STOP_COST
        or _sha256((base / "collector-cell227.log").read_bytes())
        != CELL249_METADATA_STOP_LOG_SHA256
        or _sha256(_pair_receipt_path(journal_path, 124).read_bytes()) != CELL249_PAIR124_SHA256
        or _sha256((base / "endpoint-preflight-125.json").read_bytes())
        != CELL249_ENDPOINT125_SHA256
        or _sha256((base / "provider-pause-clearance-after-cell-227.json").read_bytes())
        != PRIOR_CELL227_CLEARANCE_SHA256
        or _pair_receipt_path(journal_path, 125).exists()
        or (base / "endpoint-recheck-cell-249.json").exists()
        or (base / CELL249_METADATA_CLEARANCE_NAME).exists()
        or (base / "traces" / f"{manifest['cells'][249]['cell_id']}.json").exists()
        or (base / "stages" / f"{manifest['cells'][249]['cell_id']}.json").exists()
    ):
        raise ValueError("cell-249 stop differs from authoritative reviewed checkpoint")
    first, second = manifest["cells"][248:250]
    stopped_records = [
        row
        for row in (json.loads(line) for line in journal_path.read_bytes().splitlines())
        if row.get("type") == "cell_completed"
    ][-1:]
    if (
        len(stopped_records) != 1
        or stopped_records[0]["cell_id"] != FAILED_CELL_ID
        or stopped_records[0]["run_id"] != FAILED_RUN_ID
        or any(
            row["status"] != "provider_failed"
            or row["failure_reason"] != "provider_unavailable"
            or row["score_valid"] is not False
            for row in stopped_records
        )
        or first["cell_id"] != FAILED_CELL_ID
        or second["cell_id"] in result["cells"]
    ):
        raise ValueError("cell-249 partner or retained failure differs")
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
    metadata_approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    _require_frozen_execution_files(execution_root)
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, original_approval_path
    )
    metadata_approval_sha = _require_approval(metadata_approval_path)
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("provider key and dedicated database URL are required")
    result = await postcheck(
        source_root, protocol_root, manifest_path, original_approval_path, journal_path, state_dir
    )
    first, second = _require_stop(manifest, journal_path, result)
    base = journal_path.parent
    if metadata_approval_path != (base / "cell249-approval.json").resolve():
        raise ValueError("cell-249 approval path differs from the sealed study directory")
    if (
        _sha256((base / "cell227-approval.json").read_bytes())
        != EXPECTED_CELL227_PAUSE_APPROVAL_SHA256
    ):
        raise ValueError("prior cell-227 approval differs")
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
    if len(history) != 249 or health.provider_health(history[227:]) != "continue":
        raise ValueError("cell-228 provider-health epoch is not continuing at cell 249")
    spent = Decimal(result["estimated_model_token_cost_usd"])
    cap = Decimal(str(approval["cost_ceiling_usd"]))
    if spent + CELL_WORST_USD > cap:
        raise ValueError("insufficient approved cumulative capacity for cell 250")
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
    clearance_path = base / CELL249_METADATA_CLEARANCE_NAME
    try:
        if _sha256(journal_path.read_bytes()) != CELL249_METADATA_STOP_JOURNAL_SHA256:
            raise ValueError("journal changed after exclusive cell-249 lock")
        selected = _fresh_endpoint_check(manifest)
        original_endpoint = json.loads((base / "endpoint-preflight-125.json").read_bytes())
        if not _matching_endpoint(original_endpoint, selected):
            raise ValueError("selected endpoint or price differs from pair-125 preflight")
        if _sha256(journal_path.read_bytes()) != CELL249_METADATA_STOP_JOURNAL_SHA256:
            raise ValueError("journal changed during cell-249 route replay")
        recheck_sha = _write_json_create_only(base / "endpoint-recheck-cell-249.json", selected)
        _write_json_create_only(
            clearance_path,
            {
                "protocol": PROTOCOL,
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "original_approval_sha256": _sha256(original_approval_path.read_bytes()),
                "cell249_approval_sha256": metadata_approval_sha,
                "stop_journal_sha256": CELL249_METADATA_STOP_JOURNAL_SHA256,
                "stop_log_sha256": CELL249_METADATA_STOP_LOG_SHA256,
                "pair124_receipt_sha256": CELL249_PAIR124_SHA256,
                "endpoint_preflight_125_sha256": CELL249_ENDPOINT125_SHA256,
                "prior_cell227_clearance_sha256": PRIOR_CELL227_CLEARANCE_SHA256,
                "endpoint_recheck_cell249_sha256": recheck_sha,
                "last_failed_cell_id": FAILED_CELL_ID,
                "last_failed_run_id": FAILED_RUN_ID,
                "resume_at_order": 250,
                "provider_health_epoch_start_order": 228,
                "new_provider_health_epoch": False,
                "selected_endpoint_recheck": selected,
            },
        )
        journal.append(
            {"type": "cell_started", "cell_id": second["cell_id"], "order": 250, "at": _utc_now()}
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
            raise ValueError("cell 250 lacks a build; reconcile its started event")
        build = runtime.state.verify_build_integrity(outcome.build_id)
        if build.pair_id is None or build.spec != spec.range:
            raise ValueError("cell 250 build or fixture binding differs")
        await close_completed_monolithic_coverage(events, outcome.run_id)
        trace = await events.read_run(outcome.run_id)
        trace_path = base / "traces" / f"{second['cell_id']}.json"
        if trace_path.exists():
            raise ValueError("cell 250 trace exists before its one permitted run")
        record = _record(
            cell=second,
            order=250,
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
            raise ValueError("cell 250 differs in pair, endpoint, or approved cost")
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
        audits = [result["cells"][cell["cell_id"]] for cell in manifest["cells"][:249]]
        audits.append(partner_audit)
        await audit_pair_inventory(engine, events, journal.completed, audits)
        if (
            health.provider_health(
                history[227:]
                + (health.CellTerminal(record["status"], record.get("failure_reason")),)
            )
            != "continue"
        ):
            raise ValueError("existing provider-health epoch stopped after cell 250")
        _write_json_create_only(
            _pair_receipt_path(journal_path, 125),
            {
                "protocol": EXPECTED_PROTOCOL,
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "pair_number": 125,
                "cell_ids": [first["cell_id"], second["cell_id"]],
                "run_ids": [prior["run_id"], record["run_id"]],
                "audits": [result["cells"][first["cell_id"]], partner_audit],
                "endpoint_preflight_sha256": CELL249_ENDPOINT125_SHA256,
                "cumulative_estimated_cost_usd": str(spent),
            },
        )
    finally:
        journal.close()
        await engine.dispose()

    checked = await postcheck(
        source_root, protocol_root, manifest_path, original_approval_path, journal_path, state_dir
    )
    if checked["journaled_cells"] != 250:
        raise ValueError("cell-250 authoritative full postcheck did not close pair 125")
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
    parser.add_argument("--cell249-approval", type=Path, required=True)
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
            args.cell249_approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
