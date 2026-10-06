"""Retain the two already-executed M6.6 Range B bootstrap failures without retry."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from m66_collect_pilot import _approval_and_manifest, _canonical, _spec
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import unscored_run
from offsecgym.experiment.scripted import experiment_hash
from offsecgym.research.m64_execute import _sha256, _utc_now, _write_trace
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallStarted,
    PrerequisiteBootstrapCompleted,
    PrerequisiteBootstrapStarted,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events

STOP_JOURNAL_SHA256 = "10b205586cacccfa12d2fc4e9ef64654b2b27cfa28ee7a6d278066a5c0620695"
RUNS = (
    ("97a887b210a204ce", UUID("2a585437-aafc-44b8-8e8a-05b22f9df0ce")),
    ("b7d5e88f688119c6", UUID("b036e2b6-e434-4898-888d-cbdcd118bd43")),
)
EVENT_COUNT = 550
BUILD_ID = UUID("3f9772ed-b1a9-584e-9579-cd5b0b0501f0")


def _identity_hash(trace: list) -> str:
    items = [[event.sequence_number, str(event.event_id)] for event in trace]
    return hashlib.sha256(json.dumps(items, separators=(",", ":")).encode()).hexdigest()


def _expected_run_hash(spec, arm: str) -> str:
    if arm == "control":
        return experiment_hash(spec)
    return hashlib.sha256(
        _canonical(
            {"spec": spec.model_dump(mode="json"), "policy": "m66-generic-temporal-witness-v1"}
        )
    ).hexdigest()


async def reconcile(
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
    *,
    check_only: bool = False,
) -> dict:
    manifest, _approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, approval_path
    )
    if not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("OFFSECGYM_DATABASE_URL is required")
    if [cell["cell_id"] for cell in manifest["cells"][4:6]] != [item[0] for item in RUNS]:
        raise ValueError("frozen Range B vulnerable cells differ")
    with journal_path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = journal_path.read_bytes()
        if _sha256(before) != STOP_JOURNAL_SHA256:
            raise ValueError("stopped v1 journal differs; no reconciliation mutation allowed")
        rows = [json.loads(line) for line in before.splitlines()]
        expected_types = [
            "batch_started",
            "cell_started",
            "cell_started",
            "cell_completed",
            "cell_completed",
            "cell_started",
            "cell_started",
            "cell_completed",
            "cell_completed",
            "cell_started",
            "cell_started",
        ]
        completed = [row for row in rows if row["type"] == "cell_completed"]
        started = [row for row in rows if row["type"] == "cell_started"]
        if (
            [row["type"] for row in rows] != expected_types
            or len(completed) != 4
            or len(started) != 6
            or [row["cell_id"] for row in completed]
            != [cell["cell_id"] for cell in manifest["cells"][:4]]
            or [row["cell_id"] for row in started]
            != [cell["cell_id"] for cell in manifest["cells"][:6]]
        ):
            raise ValueError("stopped v1 journal order differs")
        for row in completed:
            path = Path(row["trace_path"])
            if not path.is_file() or _sha256(path.read_bytes()) != row["trace_sha256"]:
                raise ValueError("prior completed cell trace differs")
        receipt_path = journal_path.parent / "range-b-bootstrap-reconciliation.json"
        if receipt_path.exists():
            raise ValueError("Range B reconciliation receipt already exists")
        state = ComposeRangeRuntime(state_dir).state
        build = state.verify_build_integrity(BUILD_ID)
        fixture = json.loads((state_dir / "builds" / BUILD_ID.hex / "fixture.json").read_text())
        required_gets = len(fixture["users"]) + 5 * len(fixture["organizations"])
        if (
            build.spec.family != "enterprise_change_control_v1"
            or build.spec.seed != 704929
            or build.spec.patched
            or len(fixture["users"]) != 18
            or len(fixture["organizations"]) != 3
            or required_gets != 33
        ):
            raise ValueError("Range B fixture or deterministic bootstrap demand differs")
        engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
        try:
            events = PostgresEventStore(engine)
            records = []
            traces = []
            evidence = []
            for order, (cell_id, run_id) in enumerate(RUNS, 5):
                cell = manifest["cells"][order - 1]
                spec = _spec(protocol_root, manifest, cell)
                trace = await events.read_run(run_id)
                if (
                    len(trace) != EVENT_COUNT
                    or [event.sequence_number for event in trace] != list(range(1, EVENT_COUNT + 1))
                    or any(event.run_id != run_id for event in trace)
                ):
                    raise ValueError("Range B failure event identities differ")
                run_starts = [event for event in trace if isinstance(event, RunStarted)]
                ranges = [event for event in trace if isinstance(event, RangeStarted)]
                terminals = [event for event in trace if isinstance(event, RunCompleted)]
                boot_starts = [
                    event for event in trace if isinstance(event, PrerequisiteBootstrapStarted)
                ]
                requests = [event for event in trace if isinstance(event, ActionRequested)]
                actions = [event for event in trace if isinstance(event, ActionCompleted)]
                if (
                    len(run_starts) != 1
                    or run_starts[0].experiment_hash != _expected_run_hash(spec, cell["arm"])
                    or len(ranges) != 1
                    or ranges[0].build_id != BUILD_ID
                    or len(terminals) != 1
                    or terminals[0].status != "environment_failed"
                    or terminals[0].sequence_number != EVENT_COUNT
                    or len(boot_starts) != 1
                    or boot_starts[0].budget.max_actions != 32
                    or boot_starts[0].budget.max_http_requests != 32
                    or any(isinstance(event, PrerequisiteBootstrapCompleted) for event in trace)
                    or len(requests) != 32
                    or any(
                        event.source_phase != "bootstrap" or event.method != "GET"
                        for event in requests
                    )
                    or len(actions) != 32
                    or any(event.http_status != 200 for event in actions)
                    or any(
                        isinstance(
                            event,
                            (
                                ModelCallStarted,
                                ModelCallCompleted,
                                FindingSubmitted,
                                FindingValidated,
                            ),
                        )
                        for event in trace
                    )
                ):
                    raise ValueError("Range B stopped before model use for a different reason")
                projection = project_controller_events(trace)
                if (
                    projection.active_workers
                    or projection.active_actions
                    or projection.active_coverage
                    or projection.model_reservations
                    or any(hold.active for hold in projection.admission_holds.values())
                ):
                    raise ValueError("Range B failure has an active controller reservation")
                trace_path = journal_path.parent / "traces" / f"{cell_id}.json"
                if trace_path.exists():
                    raise ValueError("Range B failure trace already exists")
                evaluation = unscored_run("environment_failed")
                traces.append((trace_path, trace))
                records.append(
                    {
                        "type": "cell_completed",
                        "cell_id": cell_id,
                        "order": order,
                        "at": _utc_now(),
                        "run_id": str(run_id),
                        "build_id": str(BUILD_ID),
                        "pair_id": str(build.pair_id),
                        "fixture_digest": build.artifact_digests.get("fixture.json"),
                        "experiment_sha256": cell["experiment_sha256"],
                        "arm": cell["arm"],
                        "variant": cell["variant"],
                        "range_family": cell["range_family"],
                        "status": "environment_failed",
                        "score_valid": False,
                        "evaluation": evaluation.model_dump(mode="json"),
                        "failure_reason_inferred": "bootstrap_action_budget_exhausted",
                        "model_calls": 0,
                        "model_call_starts": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "estimated_cost_usd": 0.0,
                        "bootstrap_actions": 32,
                        "bootstrap_http_requests": 32,
                        "gateway_actions": 0,
                        "duration_seconds": round(
                            (terminals[0].occurred_at - run_starts[0].occurred_at).total_seconds(),
                            3,
                        ),
                        "trace_path": str(trace_path),
                        "trace_sha256": _sha256(
                            (
                                json.dumps(
                                    [event.model_dump(mode="json") for event in trace],
                                    sort_keys=True,
                                )
                                + "\n"
                            ).encode()
                        ),
                    }
                )
                evidence.append(
                    {
                        "run_id": str(run_id),
                        "event_count": EVENT_COUNT,
                        "event_identity_sha256": _identity_hash(trace),
                        "trace_sha256": records[-1]["trace_sha256"],
                        "model_calls": 0,
                        "bootstrap_requests": 32,
                    }
                )
            if check_only:
                return {
                    "cell_ids": [item[0] for item in RUNS],
                    "runs": evidence,
                    "required_bootstrap_gets": required_gets,
                    "frozen_bootstrap_cap": 32,
                    "stopped_journal_sha256": STOP_JOURNAL_SHA256,
                    "check_only": True,
                    "model_calls": 0,
                    "retry_count": 0,
                }
            for trace_path, trace in traces:
                if _write_trace(trace_path, trace) != next(
                    record["trace_sha256"]
                    for record in records
                    if record["trace_path"] == str(trace_path)
                ):
                    raise ValueError("Range B trace serialization differs")
            handle.seek(0, os.SEEK_END)
            for record in records:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            receipt = {
                "protocol": manifest["protocol"],
                "reconciled_at": _utc_now(),
                "stopped_journal_sha256": STOP_JOURNAL_SHA256,
                "reconciled_journal_sha256": _sha256(journal_path.read_bytes()),
                "cell_ids": [item[0] for item in RUNS],
                "runs": evidence,
                "fixture_users": 18,
                "fixture_organizations": 3,
                "required_bootstrap_gets": required_gets,
                "frozen_bootstrap_cap": 32,
                "score_valid": False,
                "model_calls": 0,
                "estimated_model_token_cost_usd": 0.0,
                "retry_count": 0,
                "unstarted_cells": [cell["cell_id"] for cell in manifest["cells"][6:]],
            }
            with receipt_path.open("x", encoding="utf-8") as stream:
                json.dump(receipt, stream, sort_keys=True, indent=2)
                stream.write("\n")
            return receipt
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
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    receipt = asyncio.run(
        reconcile(
            args.source_root.resolve(),
            args.protocol_root.resolve(),
            args.manifest.resolve(),
            args.approval.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
            check_only=args.check_only,
        )
    )
    print(
        json.dumps(
            {
                "reconciled_cells": receipt["cell_ids"],
                "journal_sha256": receipt.get(
                    "reconciled_journal_sha256", receipt["stopped_journal_sha256"]
                ),
                "model_calls": receipt["model_calls"],
                "retry_count": receipt["retry_count"],
                "check_only": args.check_only,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
