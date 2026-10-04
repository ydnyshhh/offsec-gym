"""Read-only audit of the completed, frozen M6.5.1 witness sample."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import evaluate_run
from offsecgym.research.m65_conversion_ledger import conversion_ledger
from offsecgym.research.m65_witness_analysis import analyze_witness_matrix
from offsecgym.research.m65_witness_execute import verify_manifest
from offsecgym.research.m65_witness_packet import ReporterPacket, build_reporter_bundle
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import ValidationContext
from offsecgym.schemas.events import (
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    ReporterFinished,
    ReporterStarted,
    RunCompleted,
    RunStarted,
    WorkerSpawned,
    parse_event,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


async def audit(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    state_dir: Path,
    analysis_path: Path,
) -> dict[str, object]:
    manifest = verify_manifest(root, manifest_path)
    rows = [json.loads(line) for line in journal_path.read_text().splitlines() if line]
    cells = sorted(manifest["cells"], key=lambda item: item["order"])
    expected_endpoint = manifest["expected_selected_endpoint"]
    endpoint = expected_endpoint["revision"], expected_endpoint["upstream_provider"]
    if len(cells) != 20 or len(rows) != 41 or len(manifest["seed_set"]) != 10:
        raise ValueError("M6.5.1 sample must contain exactly ten complete pairs")
    if any(
        {item["variant"] for item in cells if item["range_seed"] == seed}
        != {"vulnerable", "patched"}
        for seed in manifest["seed_set"]
    ):
        raise ValueError("held-out seed pair is incomplete")
    header = rows[0]
    if (
        header["type"] != "batch_started"
        or header["manifest_sha256"] != _sha256(manifest_path.read_bytes())
        or (header["expected_model_revision"], header["expected_upstream_provider"]) != endpoint
        or header["max_estimated_usd"] != 45.0
    ):
        raise ValueError("journal header differs from frozen protocol")

    state = StateStore(state_dir)
    oracle_store = StateOracleStore(state)
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    event_store = PostgresEventStore(engine)
    statuses: Counter[str] = Counter()
    reporter_statuses: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    seen_run_ids: set[str] = set()
    seen_event_ids: set[str] = set()
    completed = []
    spent = 0.0
    try:
        for order, cell in enumerate(cells, start=1):
            started, record = rows[2 * order - 1 : 2 * order + 1]
            if (
                started["type"] != "cell_started"
                or record["type"] != "cell_completed"
                or started["cell_id"] != cell["cell_id"]
                or record["cell_id"] != cell["cell_id"]
                or started["order"] != order
                or record["order"] != order
                or record["build_id"] != cell["build_id"]
            ):
                raise ValueError(f"journal differs from frozen cell {order}")
            completed.append(record)
            trace_raw = Path(record["trace_path"]).read_bytes()
            if _sha256(trace_raw) != record["trace_sha256"]:
                raise ValueError(f"trace hash differs for cell {order}")
            trace_json = json.loads(trace_raw)
            trace = [parse_event(item) for item in trace_json]
            if [item.sequence_number for item in trace] != list(range(1, len(trace) + 1)):
                raise ValueError(f"event sequence differs for cell {order}")
            observation = record["observation"]
            run_id = observation["run_id"]
            if (
                run_id in seen_run_ids
                or any(str(item.run_id) != run_id for item in trace)
                or observation["experiment_sha256"] != cell["experiment_sha256"]
            ):
                raise ValueError(f"run identity differs for cell {order}")
            seen_run_ids.add(run_id)
            event_ids = {str(item.event_id) for item in trace}
            if len(event_ids) != len(trace) or event_ids & seen_event_ids:
                raise ValueError(f"event ID reused for cell {order}")
            seen_event_ids.update(event_ids)
            authoritative = await event_store.read_run(UUID(run_id))
            if [item.model_dump(mode="json") for item in authoritative] != trace_json:
                raise ValueError(f"PostgreSQL event stream differs for cell {order}")

            run_starts = [item for item in trace if isinstance(item, RunStarted)]
            ranges = [item for item in trace if isinstance(item, RangeStarted)]
            endings = [item for item in trace if isinstance(item, RunCompleted)]
            reporter_starts = [item for item in trace if isinstance(item, ReporterStarted)]
            reporter_finishes = [item for item in trace if isinstance(item, ReporterFinished)]
            if any(
                len(items) != 1
                for items in (run_starts, ranges, endings, reporter_starts, reporter_finishes)
            ):
                raise ValueError(f"run or reporter lifecycle differs for cell {order}")
            if (
                run_starts[0].experiment_hash != cell["experiment_sha256"]
                or str(ranges[0].build_id) != cell["build_id"]
                or endings[0].status != observation["evaluation"]["status"]
                or reporter_starts[0].packet_sha256 is None
                or any(isinstance(item, WorkerSpawned) for item in trace)
            ):
                raise ValueError(f"run, build, or monolithic policy differs for cell {order}")
            bootstrap = [item for item in trace if isinstance(item, PrerequisiteBootstrapCompleted)]
            bootstrap_requests = [
                item
                for item in trace
                if isinstance(item, ActionRequested) and item.source_phase == "bootstrap"
            ]
            if (
                len(bootstrap) != 1
                or bootstrap[0].action_count != 17
                or len(bootstrap_requests) != 17
            ):
                raise ValueError(f"bootstrap boundary differs for cell {order}")
            reporter_index = trace.index(reporter_starts[0])
            if any(isinstance(item, ActionRequested) for item in trace[reporter_index + 1 :]):
                raise ValueError(f"reporter dispatched a gateway action in cell {order}")
            packet_path = state_dir / "reporter_bundles" / f"{UUID(run_id).hex}.json"
            packet = ReporterPacket.model_validate_json(packet_path.read_bytes())
            rebuilt = build_reporter_bundle(
                trace[:reporter_index], state_dir, expected_run_id=UUID(run_id)
            ).packet
            if (
                packet.model_dump(mode="json") != rebuilt.model_dump(mode="json")
                or packet.bundle_sha256 != reporter_starts[0].packet_sha256
                or packet.source_trace_sha256 != reporter_starts[0].source_trace_sha256
                or packet.source_build_id != ranges[0].build_id
                or reporter_finishes[0].reporter_id != reporter_starts[0].reporter_id
            ):
                raise ValueError(f"reporter packet or source binding differs for cell {order}")
            projection = project_controller_events(trace)
            if (
                projection.active_workers
                or projection.active_actions
                or projection.active_coverage
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError(f"controller reservations remain for cell {order}")
            calls = [item for item in trace if isinstance(item, ModelCallCompleted)]
            if not calls or {
                (item.resolved_model_revision, item.resolved_upstream_provider) for item in calls
            } != {endpoint}:
                raise ValueError(f"model endpoint differs for cell {order}")
            input_tokens = sum(item.input_tokens for item in calls)
            output_tokens = sum(item.output_tokens for item in calls)
            price = manifest["price_snapshot"]
            cost = (
                input_tokens * price["input_usd_per_million"]
                + output_tokens * price["output_usd_per_million"]
            ) / 1_000_000
            if (
                observation["input_tokens"] != input_tokens
                or observation["output_tokens"] != output_tokens
                or abs(record["estimated_cost_usd"] - round(cost, 8)) > 1e-8
            ):
                raise ValueError(f"model usage or cost differs for cell {order}")
            context = ValidationContext(
                run_id=UUID(run_id),
                range_instance_id=ranges[0].range_instance_id,
                range_generation=ranges[0].range_generation,
                build_id=ranges[0].build_id,
            )
            oracle = oracle_store.load_for_context(context)
            if sum(prop.active for prop in oracle.properties) != (
                5 if cell["variant"] == "vulnerable" else 0
            ):
                raise ValueError(f"oracle variant differs for cell {order}")
            findings = tuple(item.finding for item in trace if isinstance(item, FindingSubmitted))
            validations = tuple(item.result for item in trace if isinstance(item, FindingValidated))
            score = evaluate_run(findings, validations, oracle, status=endings[0].status)
            if not score.score_valid or score.model_dump(mode="json") != observation["evaluation"]:
                raise ValueError(f"oracle score replay differs for cell {order}")
            fixture = json.loads((state.build_dir(ranges[0].build_id) / "fixture.json").read_text())
            replayed_ledger = conversion_ledger(trace, state_dir, oracle, fixture)
            if replayed_ledger != record["conversion"]:
                raise ValueError(f"prospective ledger replay differs for cell {order}")
            spent += record["estimated_cost_usd"]
            statuses[score.status] += 1
            reporter_statuses[reporter_finishes[0].status] += 1
            totals["events"] += len(trace)
            totals["model_calls"] += len(calls)
            totals["input_tokens"] += input_tokens
            totals["output_tokens"] += output_tokens
            totals["gateway_actions_after_reporter_start"] += 0
        if spent > header["max_estimated_usd"]:
            raise ValueError("sample exceeded approved estimated cost")
        replayed_analysis = analyze_witness_matrix(manifest, completed)
        if (
            analysis_path.read_text()
            != json.dumps(replayed_analysis, indent=2, sort_keys=True) + "\n"
        ):
            raise ValueError("predeclared analysis does not replay byte for byte")
    finally:
        await engine.dispose()
    return {
        "protocol": manifest["protocol"],
        "manifest_sha256": _sha256(manifest_path.read_bytes()),
        "journal_sha256": _sha256(journal_path.read_bytes()),
        "analysis_sha256": _sha256(analysis_path.read_bytes()),
        "completed_cells": len(completed),
        "score_valid_cells": sum(statuses.values()),
        "distinct_run_ids": len(seen_run_ids),
        "distinct_event_ids": len(seen_event_ids),
        "database_matched_runs": len(seen_run_ids),
        "estimated_token_cost_usd": round(spent, 6),
        "selected_endpoint": list(endpoint),
        "statuses": dict(sorted(statuses.items())),
        "reporter_statuses": dict(sorted(reporter_statuses.items())),
        "totals": dict(sorted(totals.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                audit(
                    args.repository_root,
                    args.manifest,
                    args.journal,
                    args.state_dir,
                    args.analysis,
                )
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
