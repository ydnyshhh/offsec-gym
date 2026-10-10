"""Close cell 208 from existing evidence before an explicitly approved continuation.

The executable approval hash binds the separate scope-bound user decision.
No source run is retried. The frozen health history is replayed in full;
cell 209 is the first paid assignment after this amendment.
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

from m66_cell208_redaction_offline import CELL_ID, RUN_ID, TRACE_SHA256
from m66_confirmatory_audit import audit_cell, audit_pair_inventory
from m66_confirmatory_collect import (
    EXPECTED_APPROVAL_SHA256,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256,
    EXPECTED_PROTOCOL,
    Journal,
    PostgresEventStore,
    _approval_and_manifest,
    _fresh_endpoint_check,
    _pair_receipt_path,
    _spec,
    _write_json_create_only,
    collect,
    create_async_engine,
)
from m66_confirmatory_postcheck import _journal_records
from m66_confirmatory_postcheck import audit as postcheck
from m66_resume_after_stage_bound import _write_bytes_create_only

from offsecgym.research.m64_execute import _sha256
from offsecgym.runtime.compose import ComposeRangeRuntime

EXPECTED_CELL208_APPROVAL_SHA256 = (
    "db9cfe2c700112c30483768a18b129533aeb5074ff67c835c68358f914df2858"
)
STOP_JOURNAL_SHA256 = "e0e118e096b931e31fb99cf71c51e9c2140c89bd8e688717923aade8cf4452a3"
STOP_LOG_SHA256 = "5448c31a3f74951305e3779671fd121bb5392f3b2a590619f57bd162f7a683db"
STOP_COST = Decimal("91.340037")
CELL_207_ID = "b258694e1864b44d"
CELL_207_RUN_ID = "d887eb42-4866-43fb-aa26-31648314057e"
EXPECTED_STAGE_SHA256 = "83141f46b1393e678d4ffb99933ceba7f20bfa1b3776727ea897f59fbcedf27e"
PAIR_103_SHA256 = "3e3e954a72925e1e0f73e6f6b125726497523ec5269b9d63c6964033b76fb760"
ENDPOINT_104_SHA256 = "646dc3ec4bccdce6d6d60d6f72f9770e41f80ed693e4fb2720ab1bcdcc86572a"
METADATA_CLEARANCE_SHA256 = "7be0cb84970a38efbf8aea0ab39ba56d235bedbb6d0a259b7c72501c56816414"
RECEIPT_NAME = "cell208-redaction-reconciliation-v1.json"
PROTOCOL = "m66-confirmatory-v2-cell208-redaction-reconciliation-v1"
RETAINED_INVALID_ORDERS = [89, 90, 170, 186, 187, 188, 192]


def _require_approval(path: Path) -> str:
    if EXPECTED_CELL208_APPROVAL_SHA256 == "0" * 64 or not path.is_file():
        raise ValueError("cell-208 recovery lacks separate exact paid-call approval")
    digest = _sha256(path.read_bytes())
    if digest != EXPECTED_CELL208_APPROVAL_SHA256:
        raise ValueError("cell-208 approval artifact hash differs")
    value = json.loads(path.read_bytes())
    if (
        value.get("protocol") != PROTOCOL
        or value.get("approved_for_paid_calls") is not True
        or value.get("approval_scope")
        != "assigned_cells_209_through_280_only_no_retry_or_replacement"
        or value.get("original_manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or value.get("original_approval_sha256") != EXPECTED_APPROVAL_SHA256
        or value.get("prior_metadata_approval_sha256") != EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256
        or value.get("stop_journal_sha256") != STOP_JOURNAL_SHA256
        or value.get("stage_sha256") != EXPECTED_STAGE_SHA256
        or value.get("provider_health_epoch_start_order") != 189
        or value.get("new_provider_health_epoch") is not False
        or value.get("retained_invalid_cell_orders") != RETAINED_INVALID_ORDERS
        or value.get("retained_agent_failed_cell_order") != 162
        or Decimal(str(value.get("cost_ceiling_usd"))) != Decimal("504")
    ):
        raise ValueError("cell-208 approval does not cover this exact stop")
    return digest


def _require_stop(manifest: dict, journal_path: Path) -> tuple[list[dict], Decimal]:
    raw = journal_path.read_bytes()
    if _sha256(raw) != STOP_JOURNAL_SHA256:
        raise ValueError("cell-208 journal differs from the audited stop")
    records = _journal_records(manifest, raw)
    cost = sum((Decimal(str(item["estimated_cost_usd"])) for item in records), Decimal(0))
    base = journal_path.parent
    first, second = records[-2:]
    if (
        len(records) != 208
        or cost != STOP_COST
        or (first["cell_id"], first["run_id"]) != (CELL_207_ID, CELL_207_RUN_ID)
        or (second["cell_id"], second["run_id"], second["trace_sha256"])
        != (CELL_ID, RUN_ID, TRACE_SHA256)
        or second["status"] != "budget_exhausted"
        or second["failure_reason"] != "model_token_budget_exhausted"
        or second["score_valid"] is not True
        or _sha256((base / "collector-metadata.log").read_bytes()) != STOP_LOG_SHA256
        or _sha256(_pair_receipt_path(journal_path, 103).read_bytes()) != PAIR_103_SHA256
        or _sha256((base / "endpoint-preflight-104.json").read_bytes()) != ENDPOINT_104_SHA256
        or _sha256((base / "metadata-transport-clearance-after-cell-192.json").read_bytes())
        != METADATA_CLEARANCE_SHA256
        or (base / "stages" / f"{CELL_ID}.json").exists()
        or _pair_receipt_path(journal_path, 104).exists()
        or (base / RECEIPT_NAME).exists()
        or (base / "endpoint-preflight-105.json").exists()
    ):
        raise ValueError("cell-208 checkpoint has extra work or changed evidence")
    return records, cost


def _offline_stage_bytes(
    execution_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    trace_path: Path,
    state_dir: Path,
    output: Path,
) -> bytes:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(protocol_root / "src")
    command = [
        sys.executable,
        str(execution_root / "research_ops/m66_cell208_redaction_offline.py"),
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
    result = subprocess.run(
        command, cwd=execution_root, env=environment, capture_output=True, text=True, check=False
    )
    if result.returncode or not output.is_file():
        raise ValueError("exact cell-208 offline reconstruction failed")
    data = output.read_bytes()
    if _sha256(data) != EXPECTED_STAGE_SHA256:
        raise ValueError("cell-208 offline stage bytes differ from reviewed rehearsal")
    return data


def _pair_receipt(audits: list[dict], cost: Decimal, amendment_sha256: str) -> dict:
    if len(audits) != 2:
        raise ValueError("pair-104 closure requires both authoritative audits")
    return {
        "protocol": EXPECTED_PROTOCOL,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "pair_number": 104,
        "cell_ids": [CELL_207_ID, CELL_ID],
        "run_ids": [CELL_207_RUN_ID, RUN_ID],
        "audits": audits,
        "endpoint_preflight_sha256": ENDPOINT_104_SHA256,
        "cumulative_estimated_cost_usd": str(cost),
        "retrospective_redaction_closure": True,
        "cell208_redaction_amendment_sha256": amendment_sha256,
    }


async def resume(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    original_approval_path: Path,
    cell208_approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, original_approval_path
    )
    approval_sha256 = _require_approval(cell208_approval_path)
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("provider key and dedicated database URL are required")
    records, cost = _require_stop(manifest, journal_path)
    if cost >= Decimal(str(approval["cost_ceiling_usd"])):
        raise ValueError("approved cumulative cost cap has been reached")
    base = journal_path.parent
    if (
        _sha256((base / "metadata-approval.json").read_bytes())
        != EXPECTED_METADATA_RECOVERY_APPROVAL_SHA256
    ):
        raise ValueError("prior metadata approval differs")
    endpoint = manifest["expected_selected_endpoint"]
    journal = Journal(
        journal_path,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        expected_endpoint=(endpoint["revision"], endpoint["upstream_provider"]),
        max_estimated_usd=float(approval["cost_ceiling_usd"]),
    )
    try:
        if _sha256(journal_path.read_bytes()) != STOP_JOURNAL_SHA256:
            raise ValueError("journal changed after exclusive cell-208 lock")
        with tempfile.TemporaryDirectory(prefix="m66-cell208-redaction-") as name:
            scratch = Path(name)
            stage_data = _offline_stage_bytes(
                execution_root,
                protocol_root,
                manifest_path,
                Path(records[-1]["trace_path"]),
                state_dir,
                scratch / "stages" / f"{CELL_ID}.json",
            )
            repeated = _offline_stage_bytes(
                execution_root,
                protocol_root,
                manifest_path,
                Path(records[-1]["trace_path"]),
                state_dir,
                scratch / "stage-repeat.json",
            )
            if stage_data != repeated:
                raise ValueError("cell-208 offline reconstruction is not byte deterministic")
            engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
            events = PostgresEventStore(engine)
            state = ComposeRangeRuntime(state_dir).state
            try:
                audits = []
                for number in range(1, 104):
                    receipt = json.loads(_pair_receipt_path(journal_path, number).read_bytes())
                    audits.extend(receipt["audits"])
                pair_audits = []
                for cell, record, stage_root in (
                    (manifest["cells"][206], records[206], base),
                    (manifest["cells"][207], records[207], scratch),
                ):
                    stage = stage_root / "stages" / f"{cell['cell_id']}.json"
                    pair_audits.append(
                        await audit_cell(
                            events=events,
                            state=state,
                            manifest=manifest,
                            manifest_path=manifest_path,
                            protocol_root=protocol_root,
                            state_dir=state_dir,
                            journal_dir=stage_root,
                            cell=cell,
                            record=record,
                            spec=_spec(protocol_root, manifest, cell),
                            expected_stage_sha256=_sha256(stage.read_bytes()),
                        )
                    )
                audits.extend(pair_audits)
                await audit_pair_inventory(
                    engine,
                    events,
                    {
                        cell["cell_id"]: record
                        for cell, record in zip(manifest["cells"][:208], records, strict=True)
                    },
                    audits,
                )
                # Temporary reconstruction reads the same create-only bytes.
                # Durable receipts always name the original study paths.
                for audit in pair_audits:
                    audit["stage_path"] = str(
                        state_dir.parent / "stages" / f"{audit['cell_id']}.json"
                    )
            finally:
                await engine.dispose()
            selected = _fresh_endpoint_check(manifest)
            if any(
                selected.get(field)
                != json.loads((base / "endpoint-preflight-104.json").read_bytes()).get(field)
                for field in (
                    "endpoint",
                    "model_id",
                    "provider_name",
                    "status",
                    "input_usd_per_million",
                    "output_usd_per_million",
                )
            ):
                raise ValueError("selected endpoint or price differs after cell-208 stop")
            if _sha256(journal_path.read_bytes()) != STOP_JOURNAL_SHA256:
                raise ValueError("journal changed during cell-208 replay")
            stage_sha256 = _write_bytes_create_only(base / "stages" / f"{CELL_ID}.json", stage_data)
            amendment_sha256 = _write_json_create_only(
                base / RECEIPT_NAME,
                {
                    "protocol": PROTOCOL,
                    "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                    "original_approval_sha256": EXPECTED_APPROVAL_SHA256,
                    "cell208_approval_sha256": approval_sha256,
                    "stop_journal_sha256": STOP_JOURNAL_SHA256,
                    "stop_log_sha256": STOP_LOG_SHA256,
                    "cell_id": CELL_ID,
                    "run_id": RUN_ID,
                    "trace_sha256": TRACE_SHA256,
                    "stage_sha256": stage_sha256,
                    "event_identity_sha256": pair_audits[1]["event_identity_sha256"],
                    "reconciled_redacted_field": "budget_tokens",
                    "provider_health_epoch_start_order": 189,
                    "new_provider_health_epoch": False,
                    "selected_endpoint_recheck": selected,
                },
            )
            _write_json_create_only(
                _pair_receipt_path(journal_path, 104),
                _pair_receipt(pair_audits, cost, amendment_sha256),
            )
        checked = await postcheck(
            source_root,
            protocol_root,
            manifest_path,
            original_approval_path,
            journal_path,
            state_dir,
        )
        if (
            checked["journaled_cells"] != 208
            or Decimal(str(checked["estimated_model_token_cost_usd"])) != STOP_COST
        ):
            raise ValueError("cell-208 full authoritative replay differs")
    finally:
        journal.close()
    return await collect(
        source_root, protocol_root, manifest_path, original_approval_path, journal_path, state_dir
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--cell208-approval", type=Path, required=True)
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
            args.cell208_approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
