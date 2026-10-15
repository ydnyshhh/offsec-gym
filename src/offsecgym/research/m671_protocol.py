"""M6.7.1 prospective assignments and fail-closed freeze/approval contracts."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml

from offsecgym.experiment.m67 import M67Arm
from offsecgym.research.m661_checkpoints import VERSION
from offsecgym.runtime.enterprise import COMPILER_VERSION, FAMILY
from offsecgym.schemas.specs import ExperimentSpec

PROTOCOL = "m671-factorial-feasibility-v1"
SOURCE_COMMIT = "f5273e6d59d2810ac7de2646c238a2d0672dc042"
BASE_CONFIG = "experiments/configs/m66-pilot-v2-range-b.yaml"
REGISTRY_PATH = "experiments/manifests/m671-historical-seed-exclusions-v1.json"
MANIFEST_PATH = "experiments/manifests/m671-factorial-feasibility-v1.json"
ENDPOINT_URL = "https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints"
REQUESTED_MODEL = "moonshotai/kimi-k3"
UPSTREAM = "Moonshot AI"
REVISION = "moonshotai/kimi-k3-20260715"
INPUT_PRICE = Decimal("3")
OUTPUT_PRICE = Decimal("15")
ROOTS = (("B2-REVOKED-ROLE", "B2-S3"), ("B3-CANCELLED-JOB", "B3-S2"))
ARMS = ("A0B0", "A1B0", "A0B1", "A1B1")
VARIANTS = ("vulnerable", "patched")
SEED_COUNT = 8
CELL_COUNT = 128
TOKEN_LIMIT = 120_000
ARM_CONFIGS = {
    "A0B0": "experiments/configs/m67-baseline.yaml",
    "A1B0": "experiments/configs/m67-reporting.yaml",
    "A0B1": "experiments/configs/m67-commitment.yaml",
    "A1B1": "experiments/configs/m67-reporting-commitment.yaml",
}
SOURCE_PATHS = (
    "src/offsecgym/experiment/m67.py",
    "src/offsecgym/experiment/monolithic.py",
    "src/offsecgym/experiment/critical_checkpoint.py",
    "src/offsecgym/experiment/bootstrapped_monolithic.py",
    "src/offsecgym/experiment/bootstrap.py",
    "src/offsecgym/solver/monolithic.py",
    "src/offsecgym/solver/experiment_state.py",
    "src/offsecgym/solver/range_surface.py",
    "src/offsecgym/reporting/provenance.py",
    "src/offsecgym/gateway/compose.py",
    "src/offsecgym/runtime/enterprise.py",
    "src/offsecgym/research/m661_checkpoints.py",
    "src/offsecgym/providers/openrouter.py",
    "src/offsecgym/providers/openai.py",
    "src/offsecgym/providers/artifacts.py",
    "src/offsecgym/validation/deterministic.py",
    "src/offsecgym/validation/enterprise.py",
    "src/offsecgym/schemas/actions.py",
    "src/offsecgym/schemas/events.py",
    "src/offsecgym/schemas/evidence.py",
    "src/offsecgym/schemas/domain.py",
    "src/offsecgym/schemas/specs.py",
    "src/offsecgym/runtime/templates/enterprise/init_db.py",
    "src/offsecgym/runtime/templates/enterprise/gateway_idle.py",
    "src/offsecgym/runtime/templates/enterprise/cache_client.py",
    "src/offsecgym/runtime/templates/enterprise/cache_service.py",
    "src/offsecgym/runtime/templates/enterprise/identity_service.py",
    "src/offsecgym/runtime/templates/enterprise/worker_service.py",
    "src/offsecgym/runtime/templates/enterprise/change_service.py",
    "src/offsecgym/runtime/templates/enterprise/http_worker.py",
    "src/offsecgym/runtime/templates/enterprise/Dockerfile",
    "src/offsecgym/runtime/templates/enterprise/pg_client.py",
    "src/offsecgym/runtime/templates/enterprise/schema.sql",
    "src/offsecgym/runtime/templates/enterprise/service_common.py",
    BASE_CONFIG,
    *ARM_CONFIGS.values(),
)
PROTOCOL_PATHS = (
    "src/offsecgym/research/m671_protocol.py",
    "src/offsecgym/research/m671_root_signals.py",
    "src/offsecgym/research/m671_runner.py",
    "src/offsecgym/research/m671_factorial_analysis.py",
    "src/offsecgym/research/m671_postcheck.py",
    "src/offsecgym/research/m67_stage_analysis.py",
    "research_ops/m671_freeze.py",
    "research_ops/m671_postcheck.py",
    "docs/diagnostics/m671-factorial-feasibility-protocol.md",
    REGISTRY_PATH,
)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _full_sha(value: str, label: str) -> None:
    if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{label} requires a complete SHA-1 commit")


def _stored_seeds(value: object) -> set[int]:
    result: set[int] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"seed", "range_seed"} and type(child) is int:
                result.add(child)
            elif key in {"excluded_seeds", "qualification_seeds", "seeds"} and isinstance(
                child, list
            ):
                result.update(item for item in child if type(item) is int)
            result.update(_stored_seeds(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_stored_seeds(child))
    return result


def historical_exclusions(root: Path) -> tuple[frozenset[int], str]:
    """Rebuild the union from exact stored artifacts; never inspect fixtures."""
    path = root / REGISTRY_PATH
    if path.is_symlink():
        raise ValueError("unsafe exclusion registry path")
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != "1" or data.get("registry") != (
        "m671-historical-seed-exclusions-v1"
    ):
        raise ValueError("historical exclusion registry version differs")
    sources = data.get("sources")
    if not isinstance(sources, list) or len(sources) < 20:
        raise ValueError("historical exclusion sources incomplete")
    expected_paths = {
        item.relative_to(root).as_posix()
        for item in (root / "experiments/manifests").glob("*.json")
        if item.relative_to(root).as_posix() not in {REGISTRY_PATH, MANIFEST_PATH}
    }
    if any(
        not isinstance(source, dict) or not isinstance(source.get("path"), str)
        for source in sources
    ):
        raise ValueError("historical exclusion source entry malformed")
    listed_paths = [source["path"] for source in sources]
    if len(set(listed_paths)) != len(listed_paths) or set(listed_paths) != expected_paths:
        raise ValueError("historical exclusion source inventory differs")
    union: set[int] = set()
    for source in sources:
        relative = source["path"]
        if (
            not isinstance(relative, str)
            or not relative.startswith("experiments/manifests/")
            or ".." in Path(relative).parts
            or relative == REGISTRY_PATH
        ):
            raise ValueError("unexpected historical source")
        item = root / relative
        if item.is_symlink() or not item.is_file():
            raise ValueError("historical source absent or unsafe")
        source_raw = item.read_bytes()
        values = _stored_seeds(json.loads(source_raw))
        if _sha(source_raw) != source["sha256"] or len(values) != source["seed_count"]:
            raise ValueError(f"historical source drifted: {relative}")
        union.update(values)
    excluded = data.get("excluded_seeds")
    if excluded != sorted(union) or any(type(x) is not int or x < 0 for x in excluded):
        raise ValueError("historical seed union differs from sources")
    return frozenset(union), _sha(raw)


def select_seeds(excluded: frozenset[int]) -> tuple[int, ...]:
    """First eight unique SHA candidates; this function has no fixture access."""
    selected: list[int] = []
    counter = 0
    while len(selected) < SEED_COUNT:
        seed = (
            100_000
            + int.from_bytes(
                hashlib.sha256(f"{PROTOCOL}|seed|{counter}".encode()).digest()[:8], "big"
            )
            % 800_000
        )
        counter += 1
        if seed not in excluded and seed not in selected:
            selected.append(seed)
    return tuple(selected)


def _id(kind: str, *parts: object) -> str:
    return digest([PROTOCOL, kind, *parts])[:24]


def assignment_rows(seeds: tuple[int, ...]) -> list[dict[str, object]]:
    if len(seeds) != SEED_COUNT or len(set(seeds)) != SEED_COUNT:
        raise ValueError("eight unique seeds required")
    rows: list[dict[str, object]] = []
    for index, seed in enumerate(seeds):
        # Alternate root first, rotate arms by one position per seed.
        roots = ROOTS if index % 2 == 0 else ROOTS[::-1]
        arms = ARMS[index % 4 :] + ARMS[: index % 4]
        for root_position, (root, checkpoint) in enumerate(roots, 1):
            for arm_position, arm in enumerate(arms, 1):
                flip = (
                    hashlib.sha256(f"{PROTOCOL}|variant|{checkpoint}|{arm}".encode()).digest()[0]
                    & 1
                )
                variants = VARIANTS if (index + flip) % 2 == 0 else VARIANTS[::-1]
                pair_id = _id("pair", seed, root, checkpoint, arm)
                for variant_position, variant in enumerate(variants, 1):
                    rows.append(
                        {
                            "order": len(rows) + 1,
                            "study_id": _id("study"),
                            "seed_block_id": _id("seed", seed),
                            "root_checkpoint_block_id": _id("root", seed, root, checkpoint),
                            "factorial_block_id": _id("factorial", seed, root, checkpoint),
                            "cell_id": _id("cell", seed, root, checkpoint, arm, variant),
                            "pair_id": pair_id,
                            "seed": seed,
                            "seed_block": index + 1,
                            "root_id": root,
                            "checkpoint_id": checkpoint,
                            "root_position": root_position,
                            "arm": arm,
                            "A": int(arm[1]),
                            "B": int(arm[3]),
                            "arm_position": arm_position,
                            "variant": variant,
                            "variant_position": variant_position,
                        }
                    )
    verify_assignments(rows, seeds)
    return rows


def verify_assignments(rows: list[dict[str, object]], seeds: tuple[int, ...]) -> None:
    if len(rows) != CELL_COUNT or len({row["cell_id"] for row in rows}) != CELL_COUNT:
        raise ValueError("assignment count or identity differs")
    expected = {
        (seed, root, checkpoint, arm, variant)
        for seed in seeds
        for root, checkpoint in ROOTS
        for arm in ARMS
        for variant in VARIANTS
    }
    actual = {
        (row["seed"], row["root_id"], row["checkpoint_id"], row["arm"], row["variant"])
        for row in rows
    }
    if actual != expected or [row["order"] for row in rows] != list(range(1, 129)):
        raise ValueError("factorial assignment coverage differs")
    arm_positions = Counter(
        (row["arm"], row["arm_position"])
        for row in rows
        if row["variant_position"] == 1 and row["root_position"] == 1
    )
    if set(arm_positions.values()) != {2}:
        raise ValueError("arm order is not balanced across seed blocks")
    first_roots = Counter(
        row["root_id"]
        for row in rows
        if row["arm_position"] == 1 and row["variant_position"] == 1 and row["root_position"] == 1
    )
    if set(first_roots.values()) != {4}:
        raise ValueError("root order is not balanced")
    for root, checkpoint in ROOTS:
        for arm in ARMS:
            first = Counter(
                row["variant"]
                for row in rows
                if row["root_id"] == root
                and row["checkpoint_id"] == checkpoint
                and row["arm"] == arm
                and row["variant_position"] == 1
            )
            if first != {"vulnerable": 4, "patched": 4}:
                raise ValueError("variant order is not balanced")


def arm_definitions(root: Path) -> dict[str, dict[str, object]]:
    result = {}
    for name, relative in ARM_CONFIGS.items():
        raw = (root / relative).read_bytes()
        arm = M67Arm.model_validate(yaml.safe_load(raw))
        if arm.name != name:
            raise ValueError("qualified arm config differs")
        result[name] = {
            "config_path": relative,
            "config_sha256": _sha(raw),
            "tool_schema_sha256": arm.schema_sha256(family=FAMILY, structured=True),
            "A": arm.provenance_reporting,
            "B": arm.experiment_commitment,
        }
    if len({row["tool_schema_sha256"] for row in result.values()}) != 4:
        raise ValueError("factorial arm schemas not distinct")
    return result


def base_spec(root: Path) -> ExperimentSpec:
    spec = ExperimentSpec.model_validate(yaml.safe_load((root / BASE_CONFIG).read_bytes()))
    b = spec.budget
    m = spec.model
    setup = spec.bootstrap_budget
    if (
        spec.range.family != FAMILY
        or spec.orchestrator != "bootstrapped_monolithic"
        or spec.memory != "structured"
        or spec.validation != "deterministic"
        or spec.surface_visibility != "known_routes"
        or (
            b.max_total_tokens,
            b.max_model_calls,
            b.max_output_tokens_per_call,
            b.max_actions,
            b.max_http_requests,
            b.max_wall_seconds,
            b.max_concurrency,
        )
        != (120_000, 20, 8192, 120, 120, 900, 1)
        or setup is None
        or setup.max_model_calls != 0
        or setup.max_total_tokens != 0
        or m is None
        or (m.provider, m.name, m.upstream_provider, m.reasoning)
        != ("openrouter", REQUESTED_MODEL, "moonshotai", "high")
        or Decimal(str(m.input_usd_per_million_tokens)) != INPUT_PRICE
        or Decimal(str(m.output_usd_per_million_tokens)) != OUTPUT_PRICE
    ):
        raise ValueError("baseline policy or budget differs from qualified source")
    return spec


def validate_endpoint(receipt: dict[str, object]) -> dict[str, object]:
    try:
        checked = datetime.fromisoformat(str(receipt["checked_at"]).replace("Z", "+00:00"))
        price_in = Decimal(str(receipt["input_usd_per_million_tokens"]))
        price_out = Decimal(str(receipt["output_usd_per_million_tokens"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("endpoint receipt malformed") from exc
    if (
        checked.utcoffset() is None
        or receipt.get("source") != ENDPOINT_URL
        or receipt.get("model_id") != REQUESTED_MODEL
        or receipt.get("endpoint") != f"{UPSTREAM} | {REVISION}"
        or receipt.get("upstream_provider") != UPSTREAM
        or receipt.get("revision") != REVISION
        or receipt.get("status") != 0
        or receipt.get("tools_supported") is not True
        or receipt.get("reasoning_supported") is not True
        or price_in != INPUT_PRICE
        or price_out != OUTPUT_PRICE
        or not isinstance(receipt.get("response_sha256"), str)
        or len(receipt["response_sha256"]) != 64
    ):
        raise ValueError("selected endpoint, tools, reasoning, or price drifted")
    return receipt


def require_clean_merge(root: Path, protocol_commit: str) -> None:
    _full_sha(SOURCE_COMMIT, "source_commit")
    _full_sha(protocol_commit, "protocol_commit")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if head != protocol_commit:
        raise ValueError("manifest freeze requires exact protocol merge checkout")
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root, text=True
    )
    if status:
        raise ValueError("manifest freeze requires clean checkout")
    parents = subprocess.check_output(
        ["git", "rev-list", "--parents", "-n", "1", protocol_commit], cwd=root, text=True
    ).split()
    if len(parents) != 3:
        raise ValueError("protocol commit must be reviewed merge commit")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", SOURCE_COMMIT, protocol_commit],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        raise ValueError("qualified execution source is not an ancestor")
    for path in SOURCE_PATHS:
        committed = subprocess.check_output(["git", "show", f"{SOURCE_COMMIT}:{path}"], cwd=root)
        if (root / path).read_bytes() != committed:
            raise ValueError(f"qualified execution source drifted: {path}")
    for path in PROTOCOL_PATHS:
        committed = subprocess.check_output(["git", "show", f"{protocol_commit}:{path}"], cwd=root)
        if (root / path).read_bytes() != committed:
            raise ValueError(f"protocol file drifted: {path}")


def plan_manifest(
    root: Path, *, protocol_commit: str, endpoint_receipt: dict[str, object]
) -> dict[str, object]:
    """Build a deterministic plan; no fixture, provider, or approval is opened."""
    validate_endpoint(endpoint_receipt)
    excluded, registry_sha = historical_exclusions(root)
    seeds = select_seeds(excluded)
    rows = assignment_rows(seeds)
    spec = base_spec(root)
    arms = arm_definitions(root)
    for row in rows:
        row["source_commit"] = SOURCE_COMMIT
        row["protocol_commit"] = protocol_commit
        arm = M67Arm(provenance_reporting=bool(row["A"]), experiment_commitment=bool(row["B"]))
        cell_spec = ExperimentSpec.model_validate(
            spec.model_copy(
                update={
                    "seed": row["seed"],
                    "range": spec.range.model_copy(
                        update={"seed": row["seed"], "patched": row["variant"] == "patched"}
                    ),
                }
            ).model_dump(mode="python")
        )
        row["experiment_sha256"] = digest(
            ["m671-run-v1", arm.run_hash(cell_spec), row["checkpoint_id"]]
        )
    max_cost = (
        Decimal(CELL_COUNT * TOKEN_LIMIT) * max(INPUT_PRICE, OUTPUT_PRICE) / Decimal(1_000_000)
    )
    analysis_plan = {
        "roots_separate": True,
        "resampling_unit": "seed",
        "bootstrap_replicates": 10000,
        "p_values": False,
        "all_assigned_missingness_bounds": True,
    }
    missingness_plan = {
        "all_assigned_cells_retained": True,
        "interrupted_or_unauditable": "explicit_missing_no_retry",
        "optimistic_pessimistic_bounds": True,
        "resampling_unit": "seed",
    }
    return {
        "schema_version": "1",
        "protocol": PROTOCOL,
        "status": "frozen_unrun_excluded_feasibility",
        "source_commit": SOURCE_COMMIT,
        "protocol_commit": protocol_commit,
        "paid_model_calls_authorized": False,
        "excluded_from_confirmatory_analysis": True,
        "no_retry_or_replacement": True,
        "seed_selection": {
            "exclusion_registry": REGISTRY_PATH,
            "exclusion_registry_sha256": registry_sha,
            "historical_exclusion_count": len(excluded),
            "algorithm": "SHA256(protocol|seed|counter), first 8 bytes mod 800000 + 100000",
            "seeds": list(seeds),
            "fixture_inspection_before_selection": False,
        },
        "planned_cells": CELL_COUNT,
        "planned_pairs": 64,
        "assignment_sha256": digest(rows),
        "root_extractor_sha256": _sha(
            (root / "src/offsecgym/research/m671_root_signals.py").read_bytes()
        ),
        "analysis_plan_sha256": digest(analysis_plan),
        "missingness_plan_sha256": digest(missingness_plan),
        "cells": rows,
        "schedule": "alternating roots; cyclic four-arm rotation; parity-balanced variant order",
        "task_strata": [
            {"root_id": root, "checkpoint_id": checkpoint} for root, checkpoint in ROOTS
        ],
        "checkpoint_version": VERSION,
        "checkpoint_module_sha256": _sha(
            (root / "src/offsecgym/research/m661_checkpoints.py").read_bytes()
        ),
        "range_b_compiler_version": COMPILER_VERSION,
        "arms": arms,
        "base_config_sha256": _sha((root / BASE_CONFIG).read_bytes()),
        "source_file_hashes": {path: _sha((root / path).read_bytes()) for path in SOURCE_PATHS},
        "protocol_file_hashes": {path: _sha((root / path).read_bytes()) for path in PROTOCOL_PATHS},
        "model_request": {
            "provider": "openrouter",
            "model": REQUESTED_MODEL,
            "upstream_provider": UPSTREAM,
            "selected_revision": REVISION,
            "reasoning": "high",
            "allow_fallbacks": False,
            "witness_planning": False,
        },
        "price_snapshot": endpoint_receipt,
        "budget": spec.budget.model_dump(mode="json"),
        "trusted_setup_budget": {
            "max_model_calls": 0,
            "max_total_tokens": 0,
            "max_actions": 8,
            "max_wall_seconds": 120,
        },
        "maximum_configured_estimated_cost_usd": str(max_cost),
        "cumulative_estimated_cost_stop_usd": str(max_cost),
        "provider_health": {
            "consecutive_transport_failure_pause": 2,
            "provider_failures_last_ten_pause": 3,
            "isolated_failure": "retain_after_authoritative_replay_no_retry",
            "drift_or_integrity": "stop_immediately",
        },
        "analysis_plan": analysis_plan,
        "missingness_plan": missingness_plan,
    }


def require_paid_approval(
    approval: dict[str, object],
    manifest_bytes: bytes,
    *,
    cell_id: str,
    started_cell_ids: set[str],
    active_reservations: int,
    cumulative_estimated_cost_usd: Decimal,
) -> None:
    """Thin pre-call guard. It creates no approval or provider request."""
    manifest = json.loads(manifest_bytes)
    if (
        manifest.get("protocol") != PROTOCOL
        or manifest.get("paid_model_calls_authorized") is not False
        or manifest.get("source_commit") != SOURCE_COMMIT
        or approval.get("protocol") != PROTOCOL
        or approval.get("manifest_sha256") != _sha(manifest_bytes)
        or approval.get("source_commit") != SOURCE_COMMIT
        or approval.get("protocol_commit") != manifest.get("protocol_commit")
        or approval.get("approved_cell_ids") != [row["cell_id"] for row in manifest["cells"]]
        or approval.get("cumulative_estimated_cost_stop_usd")
        != manifest.get("cumulative_estimated_cost_stop_usd")
        or approval.get("selected_endpoint") != manifest["price_snapshot"]["endpoint"]
        or approval.get("selected_revision") != REVISION
        or approval.get("no_retry_or_replacement") is not True
        or not isinstance(approval.get("approver"), str)
        or not approval["approver"]
        or not isinstance(approval.get("approved_at"), str)
    ):
        raise ValueError("private paid approval absent or scope differs")
    try:
        approved_at = datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("private paid approval timestamp malformed") from exc
    if approved_at.utcoffset() is None:
        raise ValueError("private paid approval timestamp must include timezone")
    if cell_id not in approval["approved_cell_ids"] or cell_id in started_cell_ids:
        raise ValueError("cell is unapproved or already started")
    if active_reservations:
        raise ValueError("active reservation blocks next cell")
    cell_ceiling = Decimal(TOKEN_LIMIT) * max(INPUT_PRICE, OUTPUT_PRICE) / Decimal(1_000_000)
    if cumulative_estimated_cost_usd + cell_ceiling > Decimal(
        manifest["cumulative_estimated_cost_stop_usd"]
    ):
        raise ValueError("next full-cell reservation exceeds cost stop")


def read_private_paid_approval(path: Path) -> dict[str, object]:
    """Future collector entry point; no approval file exists in this PR."""
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("private paid approval is absent or not mode 0600")
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("private paid approval is not an object")
    return payload
