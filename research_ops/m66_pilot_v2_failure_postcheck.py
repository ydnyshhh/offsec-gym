"""Read-only closeout of the stopped M6.6 v2 excluded feasibility pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from m66_collect_pilot_v2 import EXPECTED_MANIFEST_SHA256, _approval_and_manifest, _spec
from m66_pilot_v2_audit import (
    _expected_run_hash,
    _identity_sha,
    _trace_sha,
    audit_cell,
    audit_pair_inventory,
)
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import unscored_run
from offsecgym.research.m64_execute import _sha256
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import (
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events

FINAL_JOURNAL_SHA256 = "7556e09c4cb7984d56a35ba110ff3ce40e26eaf464d74c6d0cb837a4ae97ea8b"
PRICE_IN = Decimal("0.000003")
PRICE_OUT = Decimal("0.000015")


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
    journal_bytes = journal_path.read_bytes()
    if _sha256(journal_bytes) != FINAL_JOURNAL_SHA256:
        raise ValueError("stopped v2 journal bytes differ")
    rows = [json.loads(line) for line in journal_bytes.splitlines()]
    cells = manifest["cells"]
    endpoint = manifest["expected_selected_endpoint"]
    header = rows[0]
    if (
        len(rows) != 15
        or len(cells) != 8
        or header.get("type") != "batch_started"
        or header.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or header.get("expected_model_revision") != endpoint["revision"]
        or header.get("expected_upstream_provider") != endpoint["upstream_provider"]
        or Decimal(str(header.get("max_estimated_usd"))) != Decimal("15")
    ):
        raise ValueError("stopped v2 header or seven-cell journal boundary differs")
    completed = []
    for index, cell in enumerate(cells[:7]):
        started, ended = rows[2 * index + 1 : 2 * index + 3]
        if (
            started.get("type") != "cell_started"
            or ended.get("type") != "cell_completed"
            or started.get("cell_id") != cell["cell_id"]
            or ended.get("cell_id") != cell["cell_id"]
            or started.get("order") != index + 1
            or ended.get("order") != index + 1
        ):
            raise ValueError("stopped v2 cell order or identity differs")
        completed.append(ended)
    if len({record["run_id"] for record in completed}) != 7:
        raise ValueError("stopped v2 repeats a source run")
    if (journal_path.parent / "pair-postcheck-4.json").exists():
        raise ValueError("failed fourth pair has a postcheck receipt")
    for pair_number in range(1, 5):
        endpoint_path = journal_path.parent / f"endpoint-preflight-{pair_number}.json"
        receipt = json.loads(endpoint_path.read_bytes())
        if (
            receipt.get("endpoint") != f"{endpoint['upstream_provider']} | {endpoint['revision']}"
            or receipt.get("model_id") != manifest["model_request"]["name"]
            or Decimal(str(receipt.get("input_usd_per_million"))) != Decimal("3")
            or Decimal(str(receipt.get("output_usd_per_million"))) != Decimal("15")
        ):
            raise ValueError(f"pair {pair_number} selected endpoint or price differs")
    database_url = os.getenv("OFFSECGYM_DATABASE_URL")
    if not database_url:
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    engine = create_async_engine(database_url)
    state = ComposeRangeRuntime(state_dir).state
    events = PostgresEventStore(engine)
    try:
        audits = []
        spent = Decimal(0)
        for pair_index in range(3):
            pair_number = pair_index + 1
            pair_cells = cells[2 * pair_index : 2 * pair_index + 2]
            pair_records = completed[2 * pair_index : 2 * pair_index + 2]
            receipt_path = journal_path.parent / f"pair-postcheck-{pair_number}.json"
            receipt = json.loads(receipt_path.read_bytes())
            endpoint_path = journal_path.parent / f"endpoint-preflight-{pair_number}.json"
            if (
                receipt.get("protocol") != "m66-pilot-v2"
                or receipt.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
                or receipt.get("pair_number") != pair_number
                or receipt.get("cell_ids") != [cell["cell_id"] for cell in pair_cells]
                or receipt.get("run_ids") != [record["run_id"] for record in pair_records]
                or receipt.get("endpoint_preflight_sha256") != _sha256(endpoint_path.read_bytes())
                or len(receipt.get("audits", [])) != 2
                or any(
                    (record["build_id"], record["pair_id"], record["fixture_digest"])
                    != (
                        pair_records[0]["build_id"],
                        pair_records[0]["pair_id"],
                        pair_records[0]["fixture_digest"],
                    )
                    for record in pair_records[1:]
                )
            ):
                raise ValueError(f"completed pair {pair_number} receipt differs")
            pair_audits = []
            for cell, record, prior in zip(
                pair_cells, pair_records, receipt["audits"], strict=True
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
                    expected_stage_sha256=prior["stage_sha256"],
                )
                if audited != prior:
                    raise ValueError(f"completed pair {pair_number} audit differs")
                pair_audits.append(audited)
                spent += Decimal(str(record["estimated_cost_usd"]))
            if pair_audits != receipt["audits"] or receipt.get(
                "cumulative_estimated_cost_usd"
            ) != str(spent):
                raise ValueError(f"completed pair {pair_number} cost differs")
            audits.extend(pair_audits)
        cell = cells[6]
        record = completed[6]
        run_id = UUID(record["run_id"])
        trace = await events.read_run(run_id)
        trace_path = Path(record["trace_path"])
        if (
            not trace_path.is_file()
            or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
            or _trace_sha(trace) != record["trace_sha256"]
            or [event.sequence_number for event in trace] != list(range(1, len(trace) + 1))
            or any(event.run_id != run_id for event in trace)
            or len({event.event_id for event in trace}) != len(trace)
        ):
            raise ValueError("failed cell trace differs from authoritative PostgreSQL")
        starts = [event for event in trace if isinstance(event, RunStarted)]
        ranges = [event for event in trace if isinstance(event, RangeStarted)]
        ends = [event for event in trace if isinstance(event, RunCompleted)]
        failed = [event for event in trace if isinstance(event, ModelCallFailed)]
        model_starts = [event for event in trace if isinstance(event, ModelCallStarted)]
        model_completed = [event for event in trace if isinstance(event, ModelCallCompleted)]
        bootstrap = [event for event in trace if isinstance(event, PrerequisiteBootstrapCompleted)]
        bootstrap_actions = [
            event
            for event in trace
            if isinstance(event, ActionRequested) and event.source_phase == "bootstrap"
        ]
        spec = _spec(protocol_root, manifest, cell)
        if (
            len(starts) != 1
            or starts[0].experiment_hash != _expected_run_hash(spec, cell["arm"])
            or len(ranges) != 1
            or len(ends) != 1
            or ends[0].status != "provider_failed"
            or ends[0].sequence_number != len(trace)
            or len(failed) != 1
            or failed[0].reason_code != "provider_unavailable"
            or failed[0].http_status is not None
            or len(model_starts) != 10
            or len(model_completed) != 9
            or failed[0].call_id not in {event.call_id for event in model_starts}
            or failed[0].call_id in {event.call_id for event in model_completed}
            or len({event.call_id for event in model_starts}) != 10
            or {event.call_id for event in model_completed} | {failed[0].call_id}
            != {event.call_id for event in model_starts}
            or len(bootstrap) != 1
            or bootstrap[0].action_count != 33
            or bootstrap[0].http_request_count != 33
            or len(bootstrap_actions) != 33
            or record["bootstrap_actions"] != 33
            or record["bootstrap_http_requests"] != 33
            or any(isinstance(event, (FindingSubmitted, FindingValidated)) for event in trace)
        ):
            raise ValueError("failed cell provider or terminal boundary differs")
        build = state.verify_build_integrity(ranges[0].build_id)
        if (
            str(build.build_id) != record["build_id"]
            or str(build.pair_id) != record["pair_id"]
            or build.artifact_digests.get("fixture.json") != record["fixture_digest"]
            or build.spec != spec.range
        ):
            raise ValueError("failed cell build, pair, or fixture differs")
        projection = project_controller_events(trace)
        if (
            projection.active_workers
            or projection.active_actions
            or projection.active_coverage
            or projection.model_reservations
            or any(hold.active for hold in projection.admission_holds.values())
        ):
            raise ValueError("failed cell retains an active reservation or lease")
        if any(
            event.provider != "openrouter" or event.model != manifest["model_request"]["name"]
            for event in model_starts
        ) or any(
            (event.resolved_model_revision, event.resolved_upstream_provider)
            != (endpoint["revision"], endpoint["upstream_provider"])
            for event in model_completed
        ):
            raise ValueError("failed cell model route differs")
        input_tokens = sum(event.input_tokens for event in model_completed)
        output_tokens = sum(event.output_tokens for event in model_completed)
        cost = Decimal(input_tokens) * PRICE_IN + Decimal(output_tokens) * PRICE_OUT
        if (
            record["status"] != "provider_failed"
            or record["score_valid"] is not False
            or record["failure_reason"] != "provider_unavailable"
            or record["model_call_starts"] != len(model_starts)
            or record["model_calls"] != len(model_completed)
            or record["input_tokens"] != input_tokens
            or record["output_tokens"] != output_tokens
            or input_tokens + output_tokens > cell["max_total_tokens"]
            or Decimal(str(record["estimated_cost_usd"])) != cost
            or any(event.estimated_cost_usd is None for event in model_completed)
            or abs(sum(Decimal(str(event.estimated_cost_usd)) for event in model_completed) - cost)
            > Decimal("0.000001")
            or unscored_run("provider_failed").model_dump(mode="json") != record["evaluation"]
        ):
            raise ValueError("failed cell journal, score, or model cost differs")
        spent += cost
        if spent > Decimal(str(approval["cost_ceiling_usd"])):
            raise ValueError("stopped pilot exceeded its separate v2 approval")
        failed_audit = {
            "cell_id": cell["cell_id"],
            "run_id": str(run_id),
            "event_count": len(trace),
            "event_identity_sha256": _identity_sha(trace),
            "trace_sha256": record["trace_sha256"],
            "status": "provider_failed",
            "score_valid": False,
            "model_call_starts": len(model_starts),
            "model_call_completions": len(model_completed),
            "model_call_failure_reason": failed[0].reason_code,
            "model_call_failure_http_status": failed[0].http_status,
            "bootstrap_actions": len(bootstrap_actions),
            "estimated_cost_usd": str(cost),
            "validator_replay_run_ids": [],
        }
        await audit_pair_inventory(
            engine,
            events,
            {cell["cell_id"]: record for cell, record in zip(cells[:7], completed, strict=True)},
            [*audits, failed_audit],
        )
        return {
            "protocol": "m66-pilot-v2",
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "approval_sha256": _sha256(approval_path.read_bytes()),
            "final_journal_sha256": FINAL_JOURNAL_SHA256,
            "source_commit": manifest["source_commit"],
            "protocol_commit": manifest["protocol_commit"],
            "planned_cells": 8,
            "journaled_cells": 7,
            "score_valid_cells": 6,
            "score_invalid_cells": 1,
            "completed_pairs": 3,
            "unstarted_cell_ids": [cells[7]["cell_id"]],
            "source_event_count": sum(item["event_count"] for item in audits)
            + failed_audit["event_count"],
            "validator_replay_run_ids": sorted(
                {run_id for item in audits for run_id in item["validator_replay_run_ids"]}
            ),
            "estimated_model_token_cost_usd": str(spent),
            "infrastructure_gate": "failed_provider_unavailable_cell_7",
            "policy_effect_estimate_available": False,
            "score_valid_cell_audits": audits,
            "failed_cell_audit": failed_audit,
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
                    "score_valid_cells",
                    "score_invalid_cells",
                    "estimated_model_token_cost_usd",
                    "infrastructure_gate",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
