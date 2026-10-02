"""Execute a locked M6.4 matrix with a durable, fail-closed local journal."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.evaluation import RunEvaluation
from offsecgym.experiment import WorkerExperimentRunner
from offsecgym.experiment.scripted import experiment_hash
from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.providers.openrouter import OpenRouterResponsesProvider
from offsecgym.research.m64_analysis import M64Observation, analyze_m64
from offsecgym.research.m64_matrix import plan_m64_matrix
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.events import ModelCallCompleted, RangeStarted, RunCompleted, RunStarted
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events

INPUT_USD_PER_MILLION = 3.0
OUTPUT_USD_PER_MILLION = 15.0
APPROVED_ESTIMATED_USD_LIMIT = 306.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def spec_for_cell(root: Path, manifest: dict[str, Any], cell: dict[str, Any]) -> ExperimentSpec:
    """Reconstruct a cell from its pinned arm config and verify its spec hash."""
    arm_config = manifest["arm_configs"][cell["arm"]]
    path = root / arm_config["path"]
    if _sha256(path.read_bytes()) != arm_config["sha256"]:
        raise ValueError(f"arm config changed: {path}")
    base = ExperimentSpec.model_validate(yaml.safe_load(path.read_text()))
    spec = ExperimentSpec.model_validate(
        {
            **base.model_dump(mode="json"),
            "name": (
                f"m64_{cell['arm']}_{cell['range_seed']}_"
                f"{cell['variant']}_{cell['worker_token_budget']}"
            ),
            "range": {
                **base.range.model_dump(mode="json"),
                "scenario": "tenant_boundary_v2",
                "seed": cell["range_seed"],
                "patched": cell["variant"] == "patched",
            },
            "budget": {
                **base.budget.model_dump(mode="json"),
                "max_total_tokens": cell["worker_token_budget"],
                "max_wall_seconds": manifest["worker_caps_except_tokens"]["max_wall_seconds"],
            },
        }
    )
    if experiment_hash(spec) != cell["experiment_sha256"]:
        raise ValueError(f"experiment spec differs for cell {cell['cell_id']}")
    return spec


def verify_manifest(root: Path, path: Path) -> dict[str, Any]:
    recorded = json.loads(path.read_text())
    if recorded != json.loads(
        json.dumps(plan_m64_matrix(root, source_commit=recorded["source_commit"]))
    ):
        raise ValueError("manifest no longer rebuilds exactly from pinned inputs")
    changed = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            recorded["source_commit"],
            "HEAD",
            "--",
            "src/offsecgym",
            ":!src/offsecgym/research/m64_execute.py",
            "experiments/configs",
            "examples",
        ],
        cwd=root,
        check=False,
    )
    if changed.returncode != 0:
        raise ValueError("runtime source differs from the manifest source commit")
    dirty = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "src/offsecgym",
            ":!src/offsecgym/research/m64_execute.py",
            "experiments/configs",
            "examples",
        ],
        cwd=root,
        check=False,
    )
    if dirty.returncode != 0:
        raise ValueError("runtime source has uncommitted changes")
    return recorded


class Journal:
    """An exclusive append-only record; interrupted cells cannot be retried silently."""

    def __init__(
        self,
        path: Path,
        *,
        manifest_sha256: str,
        expected_endpoint: tuple[str, str],
        max_estimated_usd: float,
    ):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+", encoding="utf-8")
        os.chmod(path, 0o600)
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise ValueError("another M6.4 collector holds the journal") from exc
        self.handle.seek(0)
        records = [json.loads(line) for line in self.handle if line.strip()]
        header = {
            "type": "batch_started",
            "manifest_sha256": manifest_sha256,
            "expected_model_revision": expected_endpoint[0],
            "expected_upstream_provider": expected_endpoint[1],
            "input_usd_per_million": INPUT_USD_PER_MILLION,
            "output_usd_per_million": OUTPUT_USD_PER_MILLION,
            "max_estimated_usd": max_estimated_usd,
        }
        if records:
            if any(records[0].get(key) != value for key, value in header.items()):
                self.close()
                raise ValueError("journal header does not match approved manifest and endpoint")
        else:
            self.append({**header, "at": _utc_now()})
            records.append(header)
        self.completed: dict[str, dict[str, Any]] = {}
        started: set[str] = set()
        for record in records[1:]:
            cell_id = record.get("cell_id")
            if record["type"] == "cell_started":
                if cell_id in started or cell_id in self.completed:
                    self.close()
                    raise ValueError("journal repeats a cell start")
                started.add(cell_id)
            elif record["type"] == "cell_completed":
                if cell_id not in started:
                    self.close()
                    raise ValueError("journal completion has no start")
                started.remove(cell_id)
                self.completed[cell_id] = record
            else:
                self.close()
                raise ValueError("unknown journal record")
        if started:
            self.close()
            raise ValueError(
                f"interrupted cell {sorted(started)[0]} requires event-store reconciliation"
            )

    def append(self, record: dict[str, Any]) -> None:
        self.handle.seek(0, os.SEEK_END)
        self.handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self) -> None:
        self.handle.close()


def _write_trace(path: Path, trace: list[Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps([event.model_dump(mode="json") for event in trace], sort_keys=True) + "\n"
    ).encode()
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        os.chmod(temporary, 0o600)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return _sha256(raw)


async def execute(
    root: Path,
    manifest_path: Path,
    journal_path: Path,
    state_dir: Path,
    *,
    expected_endpoint: tuple[str, str],
    max_estimated_usd: float,
    max_cells: int | None = None,
) -> dict[str, Any]:
    manifest = verify_manifest(root, manifest_path)
    if (
        not math.isfinite(max_estimated_usd)
        or not 0 < max_estimated_usd <= APPROVED_ESTIMATED_USD_LIMIT
    ):
        raise ValueError("estimated USD limit must be within the approved planning estimate")
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("OFFSECGYM_DATABASE_URL"):
        raise ValueError("OPENROUTER_API_KEY and OFFSECGYM_DATABASE_URL are required")
    ordered = sorted(
        (cell for cell in manifest["cells"] if cell["policy_feasible"]),
        key=lambda cell: cell["order"],
    )
    if [cell["order"] for cell in ordered] != list(range(1, len(ordered) + 1)):
        raise ValueError("manifest live orders are not consecutive")
    journal = Journal(
        journal_path,
        manifest_sha256=_sha256(manifest_path.read_bytes()),
        expected_endpoint=expected_endpoint,
        max_estimated_usd=max_estimated_usd,
    )
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    events = PostgresEventStore(engine)
    runner = WorkerExperimentRunner(
        ComposeRangeRuntime(state_dir),
        events,
        OpenRouterResponsesProvider(os.environ["OPENROUTER_API_KEY"]),
    )
    try:
        cells_by_id = {cell["cell_id"]: cell for cell in ordered}
        if set(journal.completed) - set(cells_by_id):
            raise ValueError("journal contains a cell outside the approved manifest")
        for cell_id, record in journal.completed.items():
            cell = cells_by_id[cell_id]
            if record["order"] != cell["order"]:
                raise ValueError("journal cell order differs from the approved manifest")
            observation = M64Observation.model_validate(record["observation"])
            if observation.experiment_sha256 != cell["experiment_sha256"]:
                raise ValueError("journal experiment hash differs from the approved manifest")
            trace_path = Path(record["trace_path"])
            if (
                not trace_path.is_file()
                or _sha256(trace_path.read_bytes()) != record["trace_sha256"]
            ):
                raise ValueError("journal trace export is missing or changed")
        spent = sum(record["estimated_cost_usd"] for record in journal.completed.values())
        new_cells = 0
        for cell in ordered:
            if cell["cell_id"] in journal.completed:
                continue
            if max_cells is not None and new_cells >= max_cells:
                break
            next_cell_reserve = cell["worker_token_budget"] * OUTPUT_USD_PER_MILLION / 1_000_000
            if spent + next_cell_reserve > max_estimated_usd + 1e-9:
                raise ValueError("insufficient estimated USD capacity for next cell")
            spec = spec_for_cell(root, manifest, cell)
            journal.append(
                {
                    "type": "cell_started",
                    "cell_id": cell["cell_id"],
                    "order": cell["order"],
                    "at": _utc_now(),
                }
            )
            print(
                f"starting cell={cell['order']}/{len(ordered)} id={cell['cell_id']} "
                f"arm={cell['arm']} seed={cell['range_seed']} "
                f"variant={cell['variant']} budget={cell['worker_token_budget']}",
                flush=True,
            )
            started_at = time.monotonic()
            outcome = await runner.run(spec)
            trace = await events.read_run(outcome.run_id)
            if outcome.build_id is None or str(outcome.build_id) != cell["build_id"]:
                raise ValueError(f"range build differs for cell {cell['cell_id']}")
            if not any(
                isinstance(event, RunStarted) and event.experiment_hash == cell["experiment_sha256"]
                for event in trace
            ):
                raise ValueError(f"run-start hash differs for cell {cell['cell_id']}")
            if not any(
                isinstance(event, RangeStarted) and event.build_id == outcome.build_id
                for event in trace
            ) or not any(isinstance(event, RunCompleted) for event in trace):
                raise ValueError(f"run lifecycle incomplete for cell {cell['cell_id']}")
            projection = project_controller_events(trace)
            if (
                projection.active_workers
                or projection.active_actions
                or projection.active_coverage
                or projection.model_reservations
                or any(hold.active for hold in projection.admission_holds.values())
            ):
                raise ValueError(f"controller reservations remain for cell {cell['cell_id']}")
            calls = [event for event in trace if isinstance(event, ModelCallCompleted)]
            endpoints = {
                (event.resolved_model_revision, event.resolved_upstream_provider) for event in calls
            }
            if calls and endpoints != {expected_endpoint}:
                raise ValueError(f"selected model endpoint changed for cell {cell['cell_id']}")
            input_tokens = sum(event.input_tokens for event in calls)
            output_tokens = sum(event.output_tokens for event in calls)
            estimated_cost = (
                input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION
            ) / 1_000_000
            observation = M64Observation(
                cell_id=cell["cell_id"],
                experiment_sha256=cell["experiment_sha256"],
                run_id=outcome.run_id,
                evaluation=RunEvaluation.model_validate(outcome.evaluation),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                resolved_model_revision=expected_endpoint[0],
                upstream_provider=expected_endpoint[1],
            )
            trace_path = journal_path.parent / "traces" / f"{cell['cell_id']}.json"
            trace_sha256 = _write_trace(trace_path, trace)
            record = {
                "type": "cell_completed",
                "cell_id": cell["cell_id"],
                "order": cell["order"],
                "at": _utc_now(),
                "duration_seconds": round(time.monotonic() - started_at, 3),
                "build_id": str(outcome.build_id),
                "estimated_cost_usd": round(estimated_cost, 8),
                "trace_path": str(trace_path),
                "trace_sha256": trace_sha256,
                "observation": observation.model_dump(mode="json"),
                "orchestration": orchestration_metrics(trace).model_dump(mode="json"),
                "failure_reason": outcome.failure_reason,
            }
            journal.append(record)
            journal.completed[cell["cell_id"]] = record
            spent += estimated_cost
            new_cells += 1
            print(
                f"cell={cell['order']}/{len(ordered)} id={cell['cell_id']} "
                f"run={outcome.run_id} status={outcome.evaluation.status} "
                f"tokens={input_tokens + output_tokens} estimated_usd={estimated_cost:.6f}",
                flush=True,
            )
            if spent > max_estimated_usd:
                raise ValueError("cumulative estimated USD limit exceeded")
            if not outcome.evaluation.score_valid:
                raise ValueError(
                    "unscored run recorded; inspect provider/infrastructure before resuming"
                )
        complete = len(journal.completed) == len(ordered)
        report = {
            "completed_cells": len(journal.completed),
            "planned_live_cells": len(ordered),
            "estimated_cost_usd": round(spent, 6),
            "complete": complete,
        }
        if complete:
            observations = [
                M64Observation.model_validate(journal.completed[cell["cell_id"]]["observation"])
                for cell in ordered
            ]
            analysis = analyze_m64(manifest, observations)
            analysis_path = journal_path.parent / "analysis.json"
            analysis_path.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n")
            report["analysis_path"] = str(analysis_path)
        return report
    finally:
        await engine.dispose()
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-upstream", required=True)
    parser.add_argument("--max-estimated-usd", type=float, required=True)
    parser.add_argument("--max-cells", type=int)
    args = parser.parse_args()
    result = asyncio.run(
        execute(
            args.repository_root.resolve(),
            args.manifest.resolve(),
            args.journal.resolve(),
            args.state_dir.resolve(),
            expected_endpoint=(args.expected_revision, args.expected_upstream),
            max_estimated_usd=args.max_estimated_usd,
            max_cells=args.max_cells,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
