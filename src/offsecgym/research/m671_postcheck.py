"""Read-only reconstruction contract for future M6.7.1 PostgreSQL collection."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from offsecgym.evaluation import evaluate_run, unscored_run
from offsecgym.experiment.m67 import M67Arm
from offsecgym.providers.artifacts import ModelCallArtifacts
from offsecgym.research.m67_stage_analysis import StageDataIntegrityError, analyze_run
from offsecgym.research.m661_checkpoint_analysis import (
    CheckpointDataIntegrityError,
    audit_checkpoint_model_artifacts,
    audit_checkpoint_receipt,
)
from offsecgym.research.m671_factorial_analysis import analyze_factorial
from offsecgym.research.m671_protocol import (
    CELL_COUNT,
    INPUT_PRICE,
    OUTPUT_PRICE,
    PROTOCOL,
    PROTOCOL_PATHS,
    REVISION,
    SOURCE_COMMIT,
    SOURCE_PATHS,
    TOKEN_LIMIT,
    UPSTREAM,
    arm_definitions,
    assignment_rows,
    base_spec,
    canonical,
    digest,
    historical_exclusions,
    select_seeds,
    validate_endpoint,
)
from offsecgym.research.m671_root_signals import extract_root_signals
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.events import (
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    ModelCallStarted,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.storage.tables import action_reservations, model_reservations, runs


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def trace_digest(trace: list[object]) -> str:
    return digest([event.model_dump(mode="json") for event in trace])


def journal_prefix(
    manifest: dict[str, object], raw: bytes, *, manifest_sha256: str
) -> list[dict[str, object]]:
    """Require a contiguous, no-retry prefix; an unmatched start is explicit."""
    lines = [json.loads(line) for line in raw.splitlines()]
    if (
        not lines
        or lines[0].get("type") != "batch_started"
        or lines[0].get("manifest_sha256") != manifest_sha256
        or lines[0].get("protocol") != PROTOCOL
    ):
        raise ValueError("journal header differs from manifest")
    records: list[dict[str, object]] = []
    expected = manifest["cells"]
    seen_runs: set[str] = set()
    remainder = lines[1:]
    for index in range(0, len(remainder), 2):
        if index // 2 >= CELL_COUNT:
            raise ValueError("journal exceeds frozen assignments")
        start = remainder[index]
        cell = expected[index // 2]
        if (
            start.get("type") != "cell_started"
            or start.get("cell_id") != cell["cell_id"]
            or start.get("order") != cell["order"]
            or start.get("run_id") in seen_runs
        ):
            raise ValueError("journal is not a unique frozen-order prefix")
        seen_runs.add(start["run_id"])
        if index + 1 == len(remainder):
            records.append({**start, "type": "interrupted"})
            break
        done = remainder[index + 1]
        if (
            done.get("type") != "cell_completed"
            or done.get("cell_id") != cell["cell_id"]
            or done.get("order") != cell["order"]
            or done.get("run_id") != start["run_id"]
            or done.get("experiment_sha256") != cell["experiment_sha256"]
        ):
            raise ValueError("journal start/completion differs")
        records.append(done)
    return records


def verify_manifest(manifest: dict[str, object], *, root: Path | None = None) -> None:
    if (
        manifest.get("protocol") != PROTOCOL
        or manifest.get("source_commit") != SOURCE_COMMIT
        or manifest.get("paid_model_calls_authorized") is not False
        or manifest.get("planned_cells") != CELL_COUNT
        or manifest.get("planned_pairs") != 64
        or manifest.get("no_retry_or_replacement") is not True
        or manifest.get("excluded_from_confirmatory_analysis") is not True
    ):
        raise ValueError("manifest scientific boundary differs")
    rows = manifest["cells"]
    seeds = tuple(manifest["seed_selection"]["seeds"])
    planned = assignment_rows(seeds)
    for actual, expected in zip(rows, planned, strict=True):
        if any(actual.get(key) != value for key, value in expected.items()):
            raise ValueError("assignment differs from deterministic planner")
        if actual.get("source_commit") != SOURCE_COMMIT or actual.get(
            "protocol_commit"
        ) != manifest.get("protocol_commit"):
            raise ValueError("assignment source/protocol reference differs")
    if manifest["assignment_sha256"] != digest(rows):
        raise ValueError("assignment digest differs")
    if (
        manifest.get("root_extractor_sha256")
        != manifest.get("protocol_file_hashes", {}).get(
            "src/offsecgym/research/m671_root_signals.py"
        )
        or manifest.get("analysis_plan_sha256") != digest(manifest["analysis_plan"])
        or manifest.get("missingness_plan_sha256") != digest(manifest["missingness_plan"])
        or manifest["analysis_plan"]
        != {
            "roots_separate": True,
            "resampling_unit": "seed",
            "bootstrap_replicates": 10000,
            "p_values": False,
            "all_assigned_missingness_bounds": True,
        }
        or manifest["missingness_plan"]
        != {
            "all_assigned_cells_retained": True,
            "interrupted_or_unauditable": "explicit_missing_no_retry",
            "optimistic_pessimistic_bounds": True,
            "resampling_unit": "seed",
        }
    ):
        raise ValueError("analysis or missingness plan differs")
    validate_endpoint(manifest["price_snapshot"])
    if manifest["model_request"] != {
        "provider": "openrouter",
        "model": "moonshotai/kimi-k3",
        "upstream_provider": UPSTREAM,
        "selected_revision": REVISION,
        "reasoning": "high",
        "allow_fallbacks": False,
        "witness_planning": False,
    }:
        raise ValueError("model request differs")
    configured = (
        Decimal(CELL_COUNT * TOKEN_LIMIT) * max(INPUT_PRICE, OUTPUT_PRICE) / Decimal(1_000_000)
    )
    if (
        Decimal(manifest["cumulative_estimated_cost_stop_usd"])
        != (Decimal(manifest["maximum_configured_estimated_cost_usd"]))
        or Decimal(manifest["maximum_configured_estimated_cost_usd"]) != configured
    ):
        raise ValueError("cost stop differs from configured liability")
    if root is not None:
        if set(manifest["source_file_hashes"]) != set(SOURCE_PATHS) or set(
            manifest["protocol_file_hashes"]
        ) != set(PROTOCOL_PATHS):
            raise ValueError("source/protocol file inventory differs")
        excluded, registry_sha = historical_exclusions(root)
        if (
            manifest["seed_selection"]["exclusion_registry_sha256"] != registry_sha
            or seeds != select_seeds(excluded)
            or manifest["seed_selection"]["historical_exclusion_count"] != len(excluded)
        ):
            raise ValueError("seed selection or exclusion registry differs")
        for kind, paths in (
            ("source_file_hashes", manifest["source_file_hashes"]),
            ("protocol_file_hashes", manifest["protocol_file_hashes"]),
        ):
            for relative, expected_sha in paths.items():
                path = root / relative
                if path.is_symlink() or _sha(path.read_bytes()) != expected_sha:
                    raise ValueError(f"{kind} drifted: {relative}")
        spec = base_spec(root)
        if manifest["arms"] != arm_definitions(root):
            raise ValueError("arm schema differs from frozen plan")
        for actual in rows:
            arm = M67Arm(
                provenance_reporting=bool(actual["A"]),
                experiment_commitment=bool(actual["B"]),
            )
            cell_spec = ExperimentSpec.model_validate(
                spec.model_copy(
                    update={
                        "seed": actual["seed"],
                        "range": spec.range.model_copy(
                            update={
                                "seed": actual["seed"],
                                "patched": actual["variant"] == "patched",
                            }
                        ),
                    }
                ).model_dump(mode="python")
            )
            if actual["experiment_sha256"] != digest(
                ["m671-run-v1", arm.run_hash(cell_spec), actual["checkpoint_id"]]
            ):
                raise ValueError("source experiment hash differs")


def _check_model_requests(
    state_root: Path,
    trace: list[object],
    *,
    arm_schema_sha256: str,
    receipt: dict[str, object],
    oracle: object,
) -> None:
    artifacts = ModelCallArtifacts(state_root)
    private = [
        "B2-REVOKED-ROLE",
        "B3-CANCELLED-JOB",
        "B2-S3",
        "B3-S2",
        *[str(item.property_id) for item in oracle.properties],
        *[str(item.root_cause_id) for item in oracle.root_causes],
        *receipt["setup_evidence_ids"],
        "private_anchors",
        "state_assertions",
        "setup_action_ids",
        json.dumps(receipt["private_anchors"], sort_keys=True),
        json.dumps(receipt, sort_keys=True),
    ]
    started = [item for item in trace if isinstance(item, ModelCallStarted)]
    for start in started:
        if start.request_artifact_id is None or start.request_sha256 is None:
            raise CheckpointDataIntegrityError("model request artifact absent")
        request = artifacts.read_verified(
            start.run_id,
            start.call_id,
            "request",
            start.request_artifact_id,
            start.request_sha256,
        )
        tools = request.get("tools")
        reasoning = request.get("reasoning")
        if (
            not isinstance(tools, list)
            or _sha(canonical(tools)) != arm_schema_sha256
            or request.get("model") != "moonshotai/kimi-k3"
            or request.get("provider") != {"order": ["moonshotai"], "allow_fallbacks": False}
            or not isinstance(reasoning, dict)
            or reasoning.get("effort") != "high"
        ):
            raise CheckpointDataIntegrityError("model request schema, model, or reasoning drifted")
        serialized = json.dumps(request, sort_keys=True)
        if any(value in serialized for value in private):
            raise CheckpointDataIntegrityError("private checkpoint or oracle value leaked")


def _missing(cell: dict[str, object], reason: str, *, started: bool) -> dict[str, object]:
    return {
        **cell,
        "assigned": True,
        "started": started,
        "trace_auditable": False,
        "score_valid": False,
        "resource_auditable": False,
        "terminal_status": "interrupted" if started else "unstarted",
        "missing_reason": reason,
        "model_calls": None,
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost_usd": None,
    }


async def _cell(
    cell: dict[str, object],
    record: dict[str, object],
    *,
    events: object,
    state_root: Path,
    manifest: dict[str, object],
) -> tuple[dict[str, object], set[UUID]]:
    run_id = UUID(record["run_id"])
    trace = await events.read_run(run_id)
    if not trace or trace_digest(trace) != record.get("trace_sha256"):
        return _missing(cell, "source trace absent or hash differs", started=True), {run_id}
    try:
        audit = await audit_checkpoint_receipt(state_root, events, run_id)
        receipt_path = state_root / "critical_checkpoints" / f"{run_id.hex}.json"
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(receipt_bytes)
        setup_id = UUID(receipt["setup_run_id"])
        setup_trace = await events.read_run(setup_id)
        if (
            audit["receipt_sha256"] != _sha(receipt_bytes)
            or record.get("receipt_sha256") != _sha(receipt_bytes)
            or receipt["checkpoint_id"] != cell["checkpoint_id"]
            or receipt["root_id"] != cell["root_id"]
            or receipt["seed"] != cell["seed"]
            or receipt["variant"] != cell["variant"]
            or receipt["source_commit"] != SOURCE_COMMIT
        ):
            raise CheckpointDataIntegrityError("checkpoint receipt differs from assignment")
        model_actions, model_requests = audit_checkpoint_model_artifacts(state_root, trace)
        ranges = [item for item in trace if isinstance(item, RangeStarted)]
        if len(ranges) != 1:
            raise CheckpointDataIntegrityError("range start differs")
        state = StateStore(state_root)
        build = state.verify_build_integrity(ranges[0].build_id)
        fixture = json.loads((state.build_dir(build.build_id) / "fixture.json").read_bytes())
        oracle = StateOracleStore(state)._load_ground_truth(build.build_id)
        _check_model_requests(
            state_root,
            trace,
            arm_schema_sha256=manifest["arms"][cell["arm"]]["tool_schema_sha256"],
            receipt=receipt,
            oracle=oracle,
        )
        extracted = extract_root_signals(
            receipt_bytes, trace, setup_trace, model_actions, model_requests, oracle, fixture
        )
        stage = analyze_run(
            trace, state_root, ranges[0].range_instance_id, signals=extracted.signals
        )
        if not stage["trace_auditable"]:
            raise CheckpointDataIntegrityError(str(stage["missing_reason"]))
        if (
            not isinstance(trace[0], RunStarted)
            or trace[0].experiment_hash != cell["experiment_sha256"]
            or not isinstance(trace[-1], RunCompleted)
            or trace[-1].status != record.get("terminal_status")
        ):
            raise CheckpointDataIntegrityError("source run hash or terminal differs")
        completions = [item for item in trace if isinstance(item, ModelCallCompleted)]
        if any(
            item.resolved_model_revision != REVISION or item.resolved_upstream_provider != UPSTREAM
            for item in completions
        ):
            raise CheckpointDataIntegrityError("selected endpoint drifted in response")
        findings = [item.finding for item in trace if isinstance(item, FindingSubmitted)]
        verdicts = [item.result for item in trace if isinstance(item, FindingValidated)]
        by_id = {item.finding_id: item for item in verdicts}
        if len(by_id) != len(verdicts) or any(item.finding_id not in by_id for item in findings):
            raise CheckpointDataIntegrityError("finding/validator linkage differs")
        if stage["score_valid"]:
            score = evaluate_run(
                tuple(findings),
                tuple(by_id[item.finding_id] for item in findings),
                oracle,
                status=trace[-1].status,
            )
        else:
            score = unscored_run(
                trace[-1].status,
                candidate_count=len(findings),
                validated_count=sum(item.status == "validated" for item in verdicts),
                inconclusive=sum(item.status == "inconclusive" for item in verdicts),
            )
        if score.model_dump(mode="json") != record.get("evaluation"):
            raise CheckpointDataIntegrityError("source score replay differs")
        cost = (
            sum((Decimal(str(item.estimated_cost_usd)) for item in completions), Decimal(0))
            if stage["resource_auditable"]
            and all(item.estimated_cost_usd is not None for item in completions)
            else None
        )
        if cost is not None and str(cost) != str(record.get("estimated_cost_usd")):
            raise CheckpointDataIntegrityError("journal cost differs from model usage")
        replay_ids = {
            item.result.replay_trace.replay_run_id
            for item in trace
            if isinstance(item, FindingValidated) and item.result.replay_trace is not None
        }
        row = {
            **cell,
            **stage,
            "started": True,
            "estimated_cost_usd": str(cost) if cost is not None else None,
            "root_diagnostics": extracted.diagnostics,
            "run_id": str(run_id),
            "setup_run_id": str(setup_id),
        }
        return row, {run_id, setup_id, *replay_ids}
    except (
        CheckpointDataIntegrityError,
        StageDataIntegrityError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        # Only expected source-evidence defects become assigned missingness.
        # Programming failures such as TypeError/KeyError propagate globally.
        return _missing(cell, str(exc), started=True), {run_id}


async def postcheck(
    manifest: dict[str, object],
    journal_raw: bytes,
    manifest_sha256: str,
    pair_directory: Path,
    state_root: Path,
    events: object,
    engine: object,
    *,
    protocol_root: Path | None = None,
) -> dict[str, object]:
    """Read authoritative PostgreSQL, never append, settle, or call a provider."""
    verify_manifest(manifest, root=protocol_root)
    records = journal_prefix(manifest, journal_raw, manifest_sha256=manifest_sha256)
    rows: list[dict[str, object]] = []
    referenced_runs: set[UUID] = set()
    for index, cell in enumerate(manifest["cells"]):
        if index >= len(records):
            rows.append(_missing(cell, "no source run", started=False))
        elif records[index]["type"] == "interrupted":
            rows.append(_missing(cell, "started without trustworthy completion", started=True))
            referenced_runs.add(UUID(records[index]["run_id"]))
        else:
            row, run_ids = await _cell(
                cell, records[index], events=events, state_root=state_root, manifest=manifest
            )
            rows.append(row)
            referenced_runs.update(run_ids)
    # A complete pair receipt names the exact two assigned cell IDs and source
    # runs, including explicit missingness. No receipt may be silently omitted.
    for pair_index in range(64):
        cells = manifest["cells"][2 * pair_index : 2 * pair_index + 2]
        path = pair_directory / f"pair-postcheck-{pair_index + 1}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("pair receipt absent or unsafe")
        receipt = json.loads(path.read_bytes())
        if (
            receipt.get("manifest_sha256") != manifest_sha256
            or receipt.get("pair_id") != cells[0]["pair_id"]
            or receipt.get("cell_ids") != [item["cell_id"] for item in cells]
            or receipt.get("run_ids")
            != [
                next((row.get("run_id") for row in rows if row["cell_id"] == item["cell_id"]), None)
                for item in cells
            ]
        ):
            raise ValueError("pair receipt differs from frozen assignment")
    async with engine.connect() as connection:
        actual_runs = set((await connection.execute(select(runs.c.run_id))).scalars().all())
        active_actions = (
            (
                await connection.execute(
                    select(action_reservations.c.run_id).where(
                        action_reservations.c.released_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        active_models = (
            (
                await connection.execute(
                    select(model_reservations.c.run_id).where(
                        model_reservations.c.settled_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    if actual_runs != referenced_runs or active_actions or active_models:
        raise ValueError("dedicated PostgreSQL run inventory or reservations differ")
    analysis = analyze_factorial(
        rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
    )
    raw = (
        json.dumps({"rows": rows, "analysis": analysis}, sort_keys=True, indent=2).encode() + b"\n"
    )
    duplicate = (
        json.dumps(
            {
                "rows": rows,
                "analysis": analyze_factorial(
                    rows, bootstrap_replicates=manifest["analysis_plan"]["bootstrap_replicates"]
                ),
            },
            sort_keys=True,
            indent=2,
        ).encode()
        + b"\n"
    )
    if raw != duplicate:
        raise ValueError("factorial analysis is nondeterministic")
    return {
        "protocol": PROTOCOL,
        "assigned": CELL_COUNT,
        "started": len(records),
        "trace_auditable": sum(row["trace_auditable"] is True for row in rows),
        "score_valid": sum(row["score_valid"] is True for row in rows),
        "resource_auditable": sum(row["resource_auditable"] is True for row in rows),
        "analysis_sha256": _sha(raw),
        "analysis_bytes": raw,
    }
