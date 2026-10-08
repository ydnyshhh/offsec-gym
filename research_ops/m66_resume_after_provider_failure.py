"""Resume M6.6 v2 only from the reconciled cell-89 provider failure.

This is an operational continuation of the approved frozen sample. It never
replays cell 89, changes its score, or changes the model/range protocol.
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
    CELL_WORST_USD,
    EXPECTED_MANIFEST_SHA256,
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
    _spec,
    _utc_now,
    _write_json_create_only,
    close_completed_monolithic_coverage,
    collect,
    create_async_engine,
)
from m66_confirmatory_postcheck import audit as postcheck

from offsecgym.research.m64_execute import _sha256

EXECUTION_COMMIT = "85c0c885ea6f3ed73ecc4ceaaed0adda0af29082"
STOP_JOURNAL_SHA256 = "1164b037d3f61c9ef921951b1e5d5b530ff21928d3f0251595ce18bff3d4b6ec"
STOP_CELL_ID = "d22b46a80a5008c9"
STOP_RUN_ID = "ce9a6401-17d0-49ed-a120-187b4927b6b7"
STOP_TRACE_SHA256 = "83de4d95faa25647aec58f8d1529b4cbdabdfbecb1b50b6731a0496eec54499f"
STOP_COST = Decimal("38.833569")
PAIR_NUMBER = 45


def _require_frozen_execution_files(execution_root: Path) -> None:
    """Prove the continuation imports unchanged collector/audit code."""
    for name in (
        "m66_confirmatory_collect.py",
        "m66_confirmatory_audit.py",
        "m66_confirmatory_postcheck.py",
    ):
        path = f"research_ops/{name}"
        original = subprocess.check_output(
            ["git", "show", f"{EXECUTION_COMMIT}:{path}"], cwd=execution_root
        )
        if (execution_root / path).read_bytes() != original:
            raise ValueError(f"frozen execution file changed: {path}")


def _require_stopped_prefix(manifest: dict, journal_path: Path, result: dict) -> tuple[dict, dict]:
    """Accept only the one audited partial pair and no work after it."""
    raw = journal_path.read_bytes()
    if _sha256(raw) != STOP_JOURNAL_SHA256:
        raise ValueError("stopped confirmatory prefix differs from reconciled checkpoint")
    rows = [json.loads(line) for line in raw.splitlines()]
    completed = [row for row in rows if row.get("type") == "cell_completed"]
    if (
        result["journal_sha256"] != STOP_JOURNAL_SHA256
        or result["manifest_sha256"] != EXPECTED_MANIFEST_SHA256
        or result["journaled_cells"] != 89
        or result["score_valid_cells"] != 88
        or result["provider_failed_cells"] != 1
        or Decimal(result["estimated_model_token_cost_usd"]) != STOP_COST
        or len(completed) != 89
    ):
        raise ValueError("stopped confirmatory prefix differs from reconciled checkpoint")
    first, second = manifest["cells"][88:90]
    stopped = completed[-1]
    if (
        first["cell_id"] != STOP_CELL_ID
        or stopped["cell_id"] != STOP_CELL_ID
        or stopped["run_id"] != STOP_RUN_ID
        or stopped["trace_sha256"] != STOP_TRACE_SHA256
        or stopped["order"] != 89
        or stopped["status"] != "provider_failed"
        or stopped["failure_reason"] != "provider_unavailable"
        or stopped["score_valid"] is not False
        or second["cell_id"] in result["cells"]
        or _pair_receipt_path(journal_path, PAIR_NUMBER).exists()
        or (journal_path.parent / "endpoint-recheck-cell-89.json").exists()
    ):
        raise ValueError("cell 89 or its unstarted partner differs from the stop record")
    return first, second


async def resume(
    execution_root: Path,
    source_root: Path,
    protocol_root: Path,
    manifest_path: Path,
    approval_path: Path,
    journal_path: Path,
    state_dir: Path,
) -> dict:
    _require_frozen_execution_files(execution_root)
    manifest, approval = _approval_and_manifest(
        source_root, protocol_root, manifest_path, approval_path
    )
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("approved provider key and dedicated database URL are required")
    result = await postcheck(
        source_root, protocol_root, manifest_path, approval_path, journal_path, state_dir
    )
    first, second = _require_stopped_prefix(manifest, journal_path, result)
    health = _health_module(protocol_root)
    history = tuple(
        health.CellTerminal(row["status"], row.get("failure_reason"))
        for row in (json.loads(line) for line in journal_path.read_bytes().splitlines())
        if row.get("type") == "cell_completed"
    )
    if health.provider_health(history) != "continue":
        raise ValueError("frozen provider-health gate does not admit the partner")
    spent = Decimal(result["estimated_model_token_cost_usd"])
    cap = Decimal(str(approval["cost_ceiling_usd"]))
    if spent + CELL_WORST_USD > cap:
        raise ValueError("insufficient approved cost capacity for the partner")
    spec = _spec(protocol_root, manifest, second)
    if spec != _spec(protocol_root, manifest, first):
        raise ValueError("paired arms differ in their frozen experiment spec")

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
    try:
        if _sha256(journal_path.read_bytes()) != STOP_JOURNAL_SHA256:
            raise ValueError("journal changed between replay and exclusive lock")
        # The original pair preflight remains authoritative. This create-only
        # receipt proves route recovery while holding the exclusive journal lock.
        recheck = _fresh_endpoint_check(manifest)
        original = json.loads(
            (journal_path.parent / f"endpoint-preflight-{PAIR_NUMBER}.json").read_bytes()
        )
        for field in (
            "endpoint",
            "model_id",
            "provider_name",
            "status",
            "input_usd_per_million",
            "output_usd_per_million",
        ):
            if recheck[field] != original[field]:
                raise ValueError("recovered selected endpoint differs from original pair route")
        _write_json_create_only(journal_path.parent / "endpoint-recheck-cell-89.json", recheck)
        journal.append(
            {"type": "cell_started", "cell_id": second["cell_id"], "order": 90, "at": _utc_now()}
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
            raise ValueError("partner has no build; reconcile its started row")
        build = runtime.state.verify_build_integrity(outcome.build_id)
        if build.pair_id is None or build.spec != spec.range:
            raise ValueError("partner build or fixture binding differs")
        await close_completed_monolithic_coverage(events, outcome.run_id)
        trace = await events.read_run(outcome.run_id)
        trace_path = journal_path.parent / "traces" / f"{second['cell_id']}.json"
        if trace_path.exists():
            raise ValueError("partner trace exists before its one permitted run")
        record = _record(
            cell=second,
            order=90,
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
            raise ValueError("recovered partner differs in build, endpoint, or approved cost")
        partner_audit = await audit_cell(
            events=events,
            state=runtime.state,
            manifest=manifest,
            manifest_path=manifest_path,
            protocol_root=protocol_root,
            state_dir=state_dir,
            journal_dir=journal_path.parent,
            cell=second,
            record=record,
            spec=spec,
        )
        audits = [result["cells"][cell["cell_id"]] for cell in manifest["cells"][:89]]
        audits.append(partner_audit)
        await audit_pair_inventory(engine, events, journal.completed, audits)
        if (
            health.provider_health(
                history + (health.CellTerminal(record["status"], record.get("failure_reason")),)
            )
            != "continue"
        ):
            raise ValueError("frozen provider-health gate paused after partner; no retry")
        _write_json_create_only(
            _pair_receipt_path(journal_path, PAIR_NUMBER),
            {
                "protocol": "m66-confirmatory-v1",
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "pair_number": PAIR_NUMBER,
                "cell_ids": [first["cell_id"], second["cell_id"]],
                "run_ids": [prior["run_id"], record["run_id"]],
                "audits": [result["cells"][first["cell_id"]], partner_audit],
                "endpoint_preflight_sha256": _sha256(
                    (journal_path.parent / f"endpoint-preflight-{PAIR_NUMBER}.json").read_bytes()
                ),
                "cumulative_estimated_cost_usd": str(spent),
            },
        )
    finally:
        journal.close()
        await engine.dispose()

    # Replay the complete 45-pair prefix before admitting cell 91. The frozen
    # collector then runs the remaining schedule with its original gates.
    await postcheck(
        source_root, protocol_root, manifest_path, approval_path, journal_path, state_dir
    )
    return await collect(
        source_root, protocol_root, manifest_path, approval_path, journal_path, state_dir
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
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
            args.journal.resolve(),
            args.state_dir.resolve(),
        )
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
