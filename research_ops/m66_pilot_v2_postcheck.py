"""Read-only final gate for the separately approved M6.6 v2 feasibility pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

from m66_collect_pilot_v2 import (
    EXPECTED_MANIFEST_SHA256,
    _approval_and_manifest,
    _spec,
)
from m66_pilot_v2_audit import audit_cell, audit_pair_inventory
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.research.m64_execute import _sha256
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.storage.event_store import PostgresEventStore


async def audit(
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, approval_path
    )
    raw_journal = journal_path.read_bytes()
    rows = [json.loads(line) for line in raw_journal.splitlines()]
    cells = manifest["cells"]
    if len(rows) != 17 or len(cells) != 8:
        raise ValueError("v2 final audit requires exactly eight started and completed cells")
    header = rows[0]
    endpoint = manifest["expected_selected_endpoint"]
    if (
        header.get("type") != "batch_started"
        or header.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or header.get("expected_model_revision") != endpoint["revision"]
        or header.get("expected_upstream_provider") != endpoint["upstream_provider"]
        or Decimal(str(header.get("max_estimated_usd"))) != Decimal("15")
    ):
        raise ValueError("v2 journal header differs from frozen approval")
    records = []
    for index, cell in enumerate(cells):
        started, completed = rows[2 * index + 1 : 2 * index + 3]
        if (
            started.get("type") != "cell_started"
            or completed.get("type") != "cell_completed"
            or started.get("cell_id") != cell["cell_id"]
            or completed.get("cell_id") != cell["cell_id"]
            or started.get("order") != index + 1
            or completed.get("order") != index + 1
            or completed.get("score_valid") is not True
        ):
            raise ValueError("v2 journal cell order, uniqueness, or score validity differs")
        records.append(completed)
    if len({record["run_id"] for record in records}) != 8:
        raise ValueError("v2 pilot repeated a run identity")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    engine = create_async_engine(database_url)
    state = ComposeRangeRuntime(state_dir).state
    events = PostgresEventStore(engine)
    try:
        audits = []
        cumulative_cost = Decimal(0)
        for pair_index in range(4):
            first, second = cells[2 * pair_index : 2 * pair_index + 2]
            first_record, second_record = records[2 * pair_index : 2 * pair_index + 2]
            if (
                first_record["build_id"],
                first_record["pair_id"],
                first_record["fixture_digest"],
            ) != (
                second_record["build_id"],
                second_record["pair_id"],
                second_record["fixture_digest"],
            ):
                raise ValueError("v2 paired arms did not share one build and fixture")
            pair_path = journal_path.parent / f"pair-postcheck-{pair_index + 1}.json"
            endpoint_path = journal_path.parent / f"endpoint-preflight-{pair_index + 1}.json"
            receipt = json.loads(pair_path.read_bytes())
            endpoint_receipt = json.loads(endpoint_path.read_bytes())
            if (
                receipt.get("protocol") != "m66-pilot-v2"
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("pair_number") != pair_index + 1
                or receipt.get("cell_ids") != [first["cell_id"], second["cell_id"]]
                or receipt.get("run_ids") != [first_record["run_id"], second_record["run_id"]]
                or receipt.get("endpoint_preflight_sha256") != _sha256(endpoint_path.read_bytes())
                or endpoint_receipt.get("endpoint")
                != f"{endpoint['upstream_provider']} | {endpoint['revision']}"
                or endpoint_receipt.get("model_id") != manifest["model_request"]["name"]
                or Decimal(str(endpoint_receipt.get("input_usd_per_million"))) != Decimal("3")
                or Decimal(str(endpoint_receipt.get("output_usd_per_million"))) != Decimal("15")
                or len(receipt.get("audits", [])) != 2
            ):
                raise ValueError("v2 pair receipt or live endpoint preflight differs")
            pair_audits = []
            for cell, record, prior_audit in zip(
                (first, second), (first_record, second_record), receipt["audits"], strict=True
            ):
                audited = await audit_cell(
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
                    expected_stage_sha256=prior_audit["stage_sha256"],
                )
                if audited != prior_audit:
                    raise ValueError("v2 pair receipt differs from authoritative replay")
                pair_audits.append(audited)
                cumulative_cost += Decimal(str(record["estimated_cost_usd"]))
            if pair_audits != receipt["audits"] or receipt.get(
                "cumulative_estimated_cost_usd"
            ) != str(cumulative_cost):
                raise ValueError("v2 pair-local audit or cumulative cost differs")
            audits.extend(pair_audits)
        await audit_pair_inventory(
            engine,
            events,
            {cell["cell_id"]: record for cell, record in zip(cells, records, strict=True)},
            audits,
        )
        if cumulative_cost > Decimal(str(approval["cost_ceiling_usd"])):
            raise ValueError("v2 cumulative estimated token cost exceeds its new approval")
        replay_ids = sorted(
            {value for item in audits for value in item["validator_replay_run_ids"]}
        )
        return {
            "protocol": "m66-pilot-v2",
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "approval_sha256": _sha256(approval_path.read_bytes()),
            "journal_sha256": _sha256(raw_journal),
            "source_commit": manifest["source_commit"],
            "protocol_commit": manifest["protocol_commit"],
            "planned_cells": 8,
            "journaled_cells": len(records),
            "score_valid_cells": len(records),
            "matched_pairs": 4,
            "source_event_count": sum(item["event_count"] for item in audits),
            "validator_replay_run_ids": replay_ids,
            "estimated_model_token_cost_usd": str(cumulative_cost),
            "infrastructure_gate": "passed",
            "stage_extraction_gate": "passed",
            "cells": audits,
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        audit(
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "journaled_cells",
                    "matched_pairs",
                    "estimated_model_token_cost_usd",
                    "infrastructure_gate",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
