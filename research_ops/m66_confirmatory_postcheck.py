"""Read-only PostgreSQL and artifact replay for an M6.6 confirmatory journal."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

from m66_confirmatory_audit import audit_cell, audit_pair_inventory
from m66_confirmatory_collect import (
    EXPECTED_MANIFEST_SHA256,
    _approval_and_manifest,
    _spec,
)
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.research.m64_execute import _sha256
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.storage.event_store import PostgresEventStore


def _journal_records(manifest: dict, journal_raw: bytes) -> list[dict]:
    lines = [json.loads(line) for line in journal_raw.splitlines()]
    endpoint = manifest["expected_selected_endpoint"]
    if not lines or any(
        lines[0].get(key) != expected
        for key, expected in (
            ("type", "batch_started"),
            ("manifest_sha256", EXPECTED_MANIFEST_SHA256),
            ("expected_model_revision", endpoint["revision"]),
            ("expected_upstream_provider", endpoint["upstream_provider"]),
            ("max_estimated_usd", manifest["cumulative_estimated_cost_stop_usd"]),
        )
    ):
        raise ValueError("confirmatory journal header differs from frozen manifest")
    if (len(lines) - 1) % 2:
        raise ValueError("interrupted cell requires authoritative reconciliation")
    completed = []
    for index, (started, terminal) in enumerate(zip(lines[1::2], lines[2::2], strict=True)):
        cell = manifest["cells"][index]
        if (
            started.get("type") != "cell_started"
            or terminal.get("type") != "cell_completed"
            or started.get("cell_id") != cell["cell_id"]
            or terminal.get("cell_id") != cell["cell_id"]
            or started.get("order") != index + 1
            or terminal.get("order") != index + 1
        ):
            raise ValueError("journal is not a prefix of the frozen 280-cell order")
        completed.append(terminal)
    if len(completed) > 280 or len({item["run_id"] for item in completed}) != len(completed):
        raise ValueError("confirmatory journal repeats or exceeds source runs")
    return completed


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
    raw = journal_path.read_bytes()
    completed = _journal_records(manifest, raw)
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    engine = create_async_engine(database_url)
    state = ComposeRangeRuntime(state_dir).state
    events = PostgresEventStore(engine)
    try:
        audits = []
        cumulative_cost = Decimal(0)
        for index, record in enumerate(completed):
            cell = manifest["cells"][index]
            stage_path = journal_path.parent / "stages" / f"{cell['cell_id']}.json"
            if not stage_path.is_file():
                raise ValueError("completed cell lacks pinned stage output; reconcile first")
            stage_hash = _sha256(stage_path.read_bytes())
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
                expected_stage_sha256=stage_hash,
            )
            audits.append(audited)
            cumulative_cost += Decimal(str(record["estimated_cost_usd"]))
            if cumulative_cost > Decimal(str(approval["cost_ceiling_usd"])):
                raise ValueError("confirmatory cumulative model-token cost exceeds approval")
        await audit_pair_inventory(
            engine,
            events,
            {
                cell["cell_id"]: record
                for cell, record in zip(manifest["cells"][: len(completed)], completed, strict=True)
            },
            audits,
        )
        full_pairs = len(completed) // 2
        for pair_index in range(full_pairs):
            first, second = completed[2 * pair_index : 2 * pair_index + 2]
            cells = manifest["cells"][2 * pair_index : 2 * pair_index + 2]
            pair_path = journal_path.parent / f"pair-postcheck-{pair_index + 1}.json"
            endpoint_path = journal_path.parent / f"endpoint-preflight-{pair_index + 1}.json"
            receipt = json.loads(pair_path.read_bytes())
            preflight = json.loads(endpoint_path.read_bytes())
            if (
                (first["build_id"], first["pair_id"], first["fixture_digest"])
                != (second["build_id"], second["pair_id"], second["fixture_digest"])
                or receipt.get("protocol") != "m66-confirmatory-v1"
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("pair_number") != pair_index + 1
                or receipt.get("cell_ids") != [cell["cell_id"] for cell in cells]
                or receipt.get("run_ids") != [first["run_id"], second["run_id"]]
                or receipt.get("audits") != audits[2 * pair_index : 2 * pair_index + 2]
                or receipt.get("endpoint_preflight_sha256") != _sha256(endpoint_path.read_bytes())
                or preflight.get("endpoint")
                != f"{manifest['expected_selected_endpoint']['upstream_provider']} | "
                f"{manifest['expected_selected_endpoint']['revision']}"
                or preflight.get("model_id") != manifest["model_request"]["name"]
                or preflight.get("provider_name") != "Moonshot AI"
                or preflight.get("status") != 0
                or not isinstance(preflight.get("response_sha256"), str)
                or len(preflight["response_sha256"]) != 64
                or Decimal(str(preflight.get("input_usd_per_million"))) != Decimal("3")
                or Decimal(str(preflight.get("output_usd_per_million"))) != Decimal("15")
            ):
                raise ValueError("paired build, audit, or live endpoint receipt differs")
            if first["status"] == "provider_failed":
                recheck_path = (
                    journal_path.parent / f"endpoint-recheck-cell-{2 * pair_index + 1}.json"
                )
                recheck = json.loads(recheck_path.read_bytes())
                if any(
                    recheck.get(key) != preflight.get(key)
                    for key in (
                        "endpoint",
                        "model_id",
                        "provider_name",
                        "status",
                        "input_usd_per_million",
                        "output_usd_per_million",
                    )
                ):
                    raise ValueError("isolated provider failure lacks matching route recheck")
            pair_cost = sum(
                Decimal(str(item["estimated_cost_usd"])) for item in completed[: 2 * pair_index + 2]
            )
            if receipt.get("cumulative_estimated_cost_usd") != str(pair_cost):
                raise ValueError("pair-local cumulative model-token cost differs")
        if len(completed) % 2:
            pair_index = full_pairs + 1
            endpoint_path = journal_path.parent / f"endpoint-preflight-{pair_index}.json"
            preflight = json.loads(endpoint_path.read_bytes())
            if (
                preflight.get("endpoint")
                != f"{manifest['expected_selected_endpoint']['upstream_provider']} | "
                f"{manifest['expected_selected_endpoint']['revision']}"
                or preflight.get("model_id") != manifest["model_request"]["name"]
                or preflight.get("provider_name") != "Moonshot AI"
                or preflight.get("status") != 0
                or Decimal(str(preflight.get("input_usd_per_million"))) != Decimal("3")
                or Decimal(str(preflight.get("output_usd_per_million"))) != Decimal("15")
            ):
                raise ValueError("partial pair lacks matching live endpoint preflight")
            if (journal_path.parent / f"pair-postcheck-{pair_index}.json").exists():
                raise ValueError("partial pair must not have a completed-pair receipt")
        return {
            "protocol": manifest["protocol"],
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "journal_sha256": _sha256(raw),
            "approval_sha256": _sha256(approval_path.read_bytes()),
            "source_commit": manifest["source_commit"],
            "protocol_commit": manifest["protocol_commit"],
            "planned_cells": 280,
            "journaled_cells": len(completed),
            "unstarted_cells": 280 - len(completed),
            "score_valid_cells": sum(item["score_valid"] is True for item in completed),
            "provider_failed_cells": sum(item["status"] == "provider_failed" for item in completed),
            "estimated_model_token_cost_usd": str(cumulative_cost),
            "cells": {item["cell_id"]: item for item in audits},
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
    print(json.dumps({key: value for key, value in result.items() if key != "cells"}))


if __name__ == "__main__":
    main()
