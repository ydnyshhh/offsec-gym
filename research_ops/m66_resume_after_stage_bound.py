"""Reconcile cell 162's offline stage and continue only after separate approval.

The frozen model, range, validator, stage predicates, and assignment are never
changed. The sole exception is an exact-bound in-memory packet reconstruction
for the already-failed source run. Both source cells in pair 81 are retained.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from m66_confirmatory_audit import audit_cell, audit_pair_inventory
from m66_confirmatory_collect import (
    EXPECTED_APPROVAL_SHA256,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_PAUSE_APPROVAL_SHA256,
    EXPECTED_PROTOCOL,
    PROVIDER_PAUSE_STOP_JOURNAL_SHA256,
    Journal,
    PostgresEventStore,
    _approval_and_manifest,
    _pair_receipt_path,
    _spec,
    _write_json_create_only,
    collect,
    create_async_engine,
)
from m66_confirmatory_postcheck import _journal_records
from m66_confirmatory_postcheck import audit as postcheck
from m66_stage_bound_offline import CELL_ID, EXACT_PACKET_CHARS, FROZEN_PACKET_LIMIT

from offsecgym.research.m64_execute import _sha256
from offsecgym.runtime.compose import ComposeRangeRuntime

EXPECTED_STAGE_APPROVAL_SHA256 = "0" * 64
STOP_JOURNAL_SHA256 = "02e4acbb03125bb363810980372eb5d16f1eeb324f22c2eab979d8f8a862cdbc"
STOP_COST = Decimal("71.985765")
CELL_161_ID = "00e10c134f186123"
CELL_161_RUN_ID = "8e631848-5601-40a6-b9ea-955a9d921165"
CELL_162_RUN_ID = "91c0161e-32ed-45da-bd5d-aea499625583"
CELL_162_TRACE_SHA256 = "c8f48f176e628a5fab038f2a3e7c18f009e64d51c8b1e6f2b82bbdc45a38ba0f"
RECEIPT_NAME = "stage-bound-reconciliation-cell-162.json"


def _require_stage_approval(path: Path) -> str:
    if EXPECTED_STAGE_APPROVAL_SHA256 == "0" * 64 or not path.is_file():
        raise ValueError("cell-162 offline amendment lacks separate paid continuation approval")
    digest = _sha256(path.read_bytes())
    if digest != EXPECTED_STAGE_APPROVAL_SHA256:
        raise ValueError("cell-162 approval artifact hash differs")
    value = json.loads(path.read_bytes())
    if (
        value.get("protocol") != "m66-confirmatory-v2-stage-bound-reconciliation-v1"
        or value.get("approved_for_paid_calls") is not True
        or value.get("approval_scope")
        != "assigned_cells_163_through_280_only_no_retry_or_replacement"
        or value.get("original_manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or value.get("original_approval_sha256") != EXPECTED_APPROVAL_SHA256
        or value.get("stop_journal_sha256") != STOP_JOURNAL_SHA256
        or value.get("source_run_status") != "agent_failed"
        or value.get("stage_reconstruction_packet_chars") != EXACT_PACKET_CHARS
        or Decimal(str(value.get("cost_ceiling_usd"))) != Decimal("504")
        or value.get("retained_invalid_cell_orders") != [89, 90]
        or value.get("retained_agent_failed_cell_order") != 162
    ):
        raise ValueError("cell-162 approval does not cover this exact stop")
    return digest


def _require_stop(manifest: dict, journal_path: Path) -> tuple[list[dict], Decimal]:
    raw = journal_path.read_bytes()
    if _sha256(raw) != STOP_JOURNAL_SHA256:
        raise ValueError("cell-162 journal differs from the audited stop")
    records = _journal_records(manifest, raw)
    if len(records) != 162:
        raise ValueError("cell-162 checkpoint has the wrong run count")
    cost = sum((Decimal(str(item["estimated_cost_usd"])) for item in records), Decimal(0))
    first, second = records[160:162]
    if (
        cost != STOP_COST
        or (first["cell_id"], first["run_id"]) != (CELL_161_ID, CELL_161_RUN_ID)
        or (second["cell_id"], second["run_id"]) != (CELL_ID, CELL_162_RUN_ID)
        or second["trace_sha256"] != CELL_162_TRACE_SHA256
        or second["status"] != "agent_failed"
        or second["failure_reason"] != "ValidationError"
        or second["score_valid"] is not True
        or (journal_path.parent / "stages" / f"{CELL_ID}.json").exists()
        or _pair_receipt_path(journal_path, 81).exists()
        or (journal_path.parent / RECEIPT_NAME).exists()
    ):
        raise ValueError("cell-162 checkpoint has extra work or changed failure")
    return records, cost


def _offline_stage_bytes(
    execution_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    trace_path: Path,
    state_dir: Path,
    output: Path,
) -> bytes:
    command = [
        sys.executable,
        str(execution_root / "research_ops/m66_stage_bound_offline.py"),
        "--protocol-root",
        str(protocol_root),
        "--manifest",
        str(manifest_path),
        "--trace",
        str(trace_path),
        "--state-dir",
        str(state_dir),
        "--output",
        str(output),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(protocol_root / "src")
    result = subprocess.run(
        command, cwd=execution_root, env=environment, text=True, capture_output=True, check=False
    )
    if result.returncode or not output.is_file():
        raise ValueError("exact-bound offline stage reconstruction failed")
    return output.read_bytes()


def _write_bytes_create_only(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha256(data)


def _retrospective_pair_receipt(
    *, audits: list[dict], preflight_sha256: str, cost: Decimal, amendment_sha256: str
) -> dict:
    if len(audits) != 2:
        raise ValueError("pair-81 closure requires both authoritative audits")
    return {
        "protocol": EXPECTED_PROTOCOL,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "pair_number": 81,
        "cell_ids": [CELL_161_ID, CELL_ID],
        "run_ids": [CELL_161_RUN_ID, CELL_162_RUN_ID],
        "audits": audits,
        "endpoint_preflight_sha256": preflight_sha256,
        "cumulative_estimated_cost_usd": str(cost),
        "retrospective_stage_bound_closure": True,
        "stage_bound_amendment_sha256": amendment_sha256,
    }


async def resume(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    original_approval_path: Path,
    stage_approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, original_approval_path
    )
    stage_approval_sha = _require_stage_approval(stage_approval_path)
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("provider key and dedicated database URL are required")
    records, cost = _require_stop(manifest, journal_path)
    if cost >= Decimal(str(approval["cost_ceiling_usd"])):
        raise ValueError("approved cumulative estimated-cost cap has been reached")
    prior_pause_approval = journal_path.parent / "pause-approval.json"
    prior_clearance = journal_path.parent / "provider-pause-clearance-after-cell-90.json"
    prior_pair = _pair_receipt_path(journal_path, 45)
    if (
        not prior_pause_approval.is_file()
        or not prior_clearance.is_file()
        or not prior_pair.is_file()
        or _sha256(prior_pause_approval.read_bytes()) != EXPECTED_PAUSE_APPROVAL_SHA256
    ):
        raise ValueError("prior provider-pause amendment provenance is missing")
    clearance = json.loads(prior_clearance.read_bytes())
    if (
        clearance.get("pause_approval_sha256") != EXPECTED_PAUSE_APPROVAL_SHA256
        or clearance.get("stop_journal_sha256") != PROVIDER_PAUSE_STOP_JOURNAL_SHA256
        or clearance.get("pair45_receipt_sha256") != _sha256(prior_pair.read_bytes())
    ):
        raise ValueError("prior provider-pause clearance differs")
    endpoint_path = journal_path.parent / "endpoint-preflight-81.json"
    endpoint = json.loads(endpoint_path.read_bytes())
    expected = manifest["expected_selected_endpoint"]
    if (
        endpoint.get("endpoint") != f"{expected['upstream_provider']} | {expected['revision']}"
        or endpoint.get("model_id") != manifest["model_request"]["name"]
        or endpoint.get("provider_name") != expected["upstream_provider"]
        or endpoint.get("status") != 0
        or Decimal(str(endpoint.get("input_usd_per_million"))) != Decimal("3")
        or Decimal(str(endpoint.get("output_usd_per_million"))) != Decimal("15")
    ):
        raise ValueError("pair-81 selected endpoint or price differs")

    journal = Journal(
        journal_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=(expected["revision"], expected["upstream_provider"]),
        max_estimated_usd=float(approval["cost_ceiling_usd"]),
    )
    try:
        if _sha256(journal_path.read_bytes()) != STOP_JOURNAL_SHA256:
            raise ValueError("journal changed after exclusive lock")
        with tempfile.TemporaryDirectory(prefix="m66-stage-bound-") as scratch_name:
            scratch = Path(scratch_name)
            trace_path = Path(records[161]["trace_path"])
            stage_bytes = _offline_stage_bytes(
                execution_root,
                protocol_root,
                manifest_path,
                trace_path,
                state_dir,
                scratch / "stages" / f"{CELL_ID}.json",
            )
            repeated = _offline_stage_bytes(
                execution_root,
                protocol_root,
                manifest_path,
                trace_path,
                state_dir,
                scratch / "stage-repeat.json",
            )
            if stage_bytes != repeated:
                raise ValueError("offline stage reconstruction is not byte deterministic")
            stage = json.loads(stage_bytes)
            if (
                stage.get("protocol") != EXPECTED_PROTOCOL
                or stage.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or stage.get("cell_id") != CELL_ID
                or stage.get("trace_sha256") != CELL_162_TRACE_SHA256
                or stage.get("stages", {}).get("status") != "agent_failed"
                or stage.get("stages", {}).get("score_valid") is not True
            ):
                raise ValueError("reconstructed stage differs from retained source")

            engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
            events = PostgresEventStore(engine)
            state = ComposeRangeRuntime(state_dir).state
            try:
                prior_audits = []
                for number in range(1, 81):
                    receipt = json.loads(_pair_receipt_path(journal_path, number).read_bytes())
                    prior_audits.extend(receipt["audits"])
                first_cell, second_cell = manifest["cells"][160:162]
                first_stage = journal_path.parent / "stages" / f"{CELL_161_ID}.json"
                first_audit = await audit_cell(
                    events=events,
                    state=state,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    protocol_root=protocol_root,
                    state_dir=state_dir,
                    journal_dir=journal_path.parent,
                    cell=first_cell,
                    record=records[160],
                    spec=_spec(protocol_root, manifest, first_cell),
                    expected_stage_sha256=_sha256(first_stage.read_bytes()),
                )
                second_audit = await audit_cell(
                    events=events,
                    state=state,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    protocol_root=protocol_root,
                    state_dir=state_dir,
                    journal_dir=scratch,
                    cell=second_cell,
                    record=records[161],
                    spec=_spec(protocol_root, manifest, second_cell),
                    expected_stage_sha256=_sha256(stage_bytes),
                )
                completed = {
                    cell["cell_id"]: record
                    for cell, record in zip(manifest["cells"][:162], records, strict=True)
                }
                await audit_pair_inventory(
                    engine, events, completed, prior_audits + [first_audit, second_audit]
                )
            finally:
                await engine.dispose()

            stage_path = journal_path.parent / "stages" / f"{CELL_ID}.json"
            stage_sha = _write_bytes_create_only(stage_path, stage_bytes)
            amendment_sha = _write_json_create_only(
                journal_path.parent / RECEIPT_NAME,
                {
                    "protocol": "m66-confirmatory-v2-stage-bound-reconciliation-v1",
                    "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                    "original_approval_sha256": _sha256(original_approval_path.read_bytes()),
                    "stage_approval_sha256": stage_approval_sha,
                    "stop_journal_sha256": STOP_JOURNAL_SHA256,
                    "cell_id": CELL_ID,
                    "run_id": CELL_162_RUN_ID,
                    "trace_sha256": CELL_162_TRACE_SHA256,
                    "stage_sha256": stage_sha,
                    "event_identity_sha256": second_audit["event_identity_sha256"],
                    "frozen_packet_limit_chars": FROZEN_PACKET_LIMIT,
                    "reconstructed_packet_chars": EXACT_PACKET_CHARS,
                    "source_status": "agent_failed",
                    "source_score_valid": True,
                    "offline_only": True,
                },
            )
            second_audit["stage_path"] = str(stage_path)
            _write_json_create_only(
                _pair_receipt_path(journal_path, 81),
                _retrospective_pair_receipt(
                    audits=[first_audit, second_audit],
                    preflight_sha256=_sha256(endpoint_path.read_bytes()),
                    cost=cost,
                    amendment_sha256=amendment_sha,
                ),
            )
        checked = await postcheck(
            source_root,
            protocol_root,
            manifest_path,
            original_approval_path,
            journal_path,
            state_dir,
        )
        if checked["journaled_cells"] != 162:
            raise ValueError("stage-bound closure changed the frozen run inventory")
    finally:
        journal.close()

    return await collect(
        source_root,
        protocol_root,
        manifest_path,
        original_approval_path,
        journal_path,
        state_dir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--stage-approval", type=Path, required=True)
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
            args.stage_approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
