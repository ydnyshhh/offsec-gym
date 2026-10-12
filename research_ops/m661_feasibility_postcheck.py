"""Read-only authoritative replay of an M6.6.1 excluded feasibility prefix."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

from m661_feasibility_collect import (
    CAP,
    EXPECTED_MANIFEST_SHA256,
    REVISION,
    UPSTREAM,
    _approval_and_manifest,
    _audit_cell,
    _health_decision,
    _inventory,
    _load_module,
    _sha256,
)
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.storage.event_store import PostgresEventStore


def _journal_records(manifest: dict, raw: bytes) -> list[dict]:
    lines = [json.loads(line) for line in raw.splitlines()]
    if not lines or any(
        lines[0].get(key) != value
        for key, value in (
            ("type", "batch_started"),
            ("manifest_sha256", EXPECTED_MANIFEST_SHA256),
            ("expected_model_revision", REVISION),
            ("expected_upstream_provider", UPSTREAM),
            ("input_usd_per_million", 3.0),
            ("output_usd_per_million", 15.0),
            ("max_estimated_usd", float(CAP)),
        )
    ):
        raise ValueError("journal header differs from frozen approval")
    if (len(lines) - 1) % 2:
        raise ValueError("interrupted cell requires separate PostgreSQL reconciliation")
    completed: list[dict] = []
    for index, (started, record) in enumerate(zip(lines[1::2], lines[2::2], strict=True)):
        if index >= len(manifest["cells"]):
            raise ValueError("journal exceeds frozen assignments")
        cell = manifest["cells"][index]
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
            )
        ):
            raise ValueError("journal is not a prefix of frozen order")
        completed.append(record)
    if len({item["run_id"] for item in completed}) != len(completed):
        raise ValueError("journal repeats a source run")
    return completed


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
    if journal_path.parent != approval_path.parent or state_dir.parent != approval_path.parent:
        raise ValueError("private evidence path differs")
    raw = journal_path.read_bytes()
    records = _journal_records(manifest, raw)
    if require_complete and len(records) != 96:
        raise ValueError("feasibility sample did not complete all 96 cells")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("dedicated database URL is required")
    engine = create_async_engine(database_url)
    events = PostgresEventStore(engine)
    state = ComposeRangeRuntime(state_dir).state
    analysis = _load_module(
        protocol_root / "src/offsecgym/research/m661_checkpoint_analysis.py",
        "_m661_pinned_postcheck_analysis",
    )
    try:
        audits: list[dict] = []
        rows: list[dict] = []
        cumulative = Decimal(0)
        conservative = Decimal(0)
        for index, record in enumerate(records):
            cell = manifest["cells"][index]
            audit = await _audit_cell(events, state, analysis, cell, record, None)
            audits.append(audit)
            rows.append(json.loads(Path(record["stage_path"]).read_bytes()))
            cumulative += Decimal(str(record["estimated_cost_usd"]))
            conservative += (
                Decimal(str(record["estimated_cost_usd"]))
                if record["resource_auditable"]
                else Decimal("1.80")
            )
            if cumulative > CAP or conservative > CAP:
                raise ValueError("cumulative approved cost stop exceeded")
            if _health_decision(records[: index + 1]) != "continue" and index + 1 < len(records):
                raise ValueError("collector continued after provider-health stop")
        await _inventory(engine, events, records, audits)
        pair_count = len(records) // 2
        for pair_number in range(1, pair_count + 1):
            pair = manifest["cells"][2 * pair_number - 2 : 2 * pair_number]
            receipt_path = journal_path.parent / f"pair-postcheck-{pair_number}.json"
            endpoint_path = journal_path.parent / f"endpoint-preflight-{pair_number}.json"
            receipt = json.loads(receipt_path.read_bytes())
            endpoint_raw = endpoint_path.read_bytes()
            endpoint = json.loads(endpoint_raw)
            if (
                receipt.get("protocol") != manifest["protocol"]
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("pair_number") != pair_number
                or receipt.get("cell_ids") != [cell["cell_id"] for cell in pair]
                or receipt.get("run_ids")
                != [record["run_id"] for record in records[2 * pair_number - 2 : 2 * pair_number]]
                or receipt.get("audits") != audits[2 * pair_number - 2 : 2 * pair_number]
                or receipt.get("endpoint_preflight_sha256") != _sha256(endpoint_raw)
                or endpoint.get("endpoint") != f"{UPSTREAM} | {REVISION}"
                or endpoint.get("input_usd_per_million_tokens") != 3.0
                or endpoint.get("output_usd_per_million_tokens") != 15.0
            ):
                raise ValueError("pair or selected endpoint receipt differs")
            pair_cost = sum(
                (Decimal(str(item["estimated_cost_usd"])) for item in records[: 2 * pair_number]),
                Decimal(0),
            )
            if Decimal(receipt["cumulative_estimated_cost_usd"]) != pair_cost:
                raise ValueError("pair cumulative cost replay differs")
        if len(list(journal_path.parent.glob("pair-postcheck-*.json"))) != pair_count:
            raise ValueError("extra or missing pair receipt")
        if len(list(journal_path.parent.glob("endpoint-preflight-*.json"))) != (
            pair_count + (len(records) % 2)
        ):
            raise ValueError("extra or missing endpoint preflight")
        for cell in manifest["cells"][len(records) :]:
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
            "completed_cells": len(records),
            "planned_cells": len(manifest["cells"]),
            "completed_pairs": pair_count,
            "journal_sha256": _sha256(raw),
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
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--allow-prefix", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        postcheck(
            args.execution_root.resolve(),
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
            require_complete=not args.allow_prefix,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
