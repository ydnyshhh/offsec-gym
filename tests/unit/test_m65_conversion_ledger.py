"""Prospective root stages are audited from the frozen probe boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_m65_witness_packet import _trace
from test_saas_compiler import load_spec

from offsecgym.research.m65_conversion_ledger import conversion_ledger
from offsecgym.research.m65_witness_packet import build_reporter_bundle
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.saas import SaasRangeCompiler
from offsecgym.schemas.events import ReporterFinished, ReporterStarted, RunCompleted
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.schemas.specs import Budget


def _case(tmp_path: Path, *, patched: bool = False):
    state = StateStore(tmp_path)
    build = SaasRangeCompiler(state).build(load_spec(patched))
    oracle = GroundTruthManifest.model_validate_json(
        (tmp_path / "oracles" / build.build_id.hex / "ground_truth.json").read_bytes()
    )
    fixture = json.loads((state.build_dir(build.build_id) / "fixture.json").read_text())
    run_id, trace, _, _, _, _ = _trace(tmp_path)
    trace[1] = trace[1].model_copy(update={"build_id": build.build_id})
    bundle = build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    digest = hashlib.sha256(bundle.packet.model_dump_json().encode()).hexdigest()
    reporter_id, task_id = run_id, trace[1].build_id
    trace.extend(
        [
            ReporterStarted(
                run_id=run_id,
                actor="controller",
                sequence_number=7,
                reporter_id=reporter_id,
                task_id=task_id,
                packet_sha256=digest,
                budget=Budget(max_total_tokens=30_000, max_model_calls=2),
            ),
            ReporterFinished(
                run_id=run_id,
                actor="controller",
                sequence_number=8,
                reporter_id=reporter_id,
                task_id=task_id,
                status="completed",
            ),
            RunCompleted(run_id=run_id, actor="controller", sequence_number=9, status="completed"),
        ]
    )
    return trace, oracle, fixture


def test_ledger_counts_each_vulnerable_root_once_and_checks_packet(tmp_path: Path) -> None:
    trace, oracle, fixture = _case(tmp_path)
    result = conversion_ledger(trace, tmp_path, oracle, fixture)
    assert result["score_valid"]
    assert result["score"]["false_negatives"] == 5
    assert len(result["rows"]) == 5
    assert {row["root"] for row in result["rows"]} == {prop.slug for prop in oracle.properties}
    assert not any(row["incremental_reporter_root"] for row in result["rows"])
    trace[6] = trace[6].model_copy(update={"packet_sha256": "0" * 64})
    with pytest.raises(ValueError, match="packet hash"):
        conversion_ledger(trace, tmp_path, oracle, fixture)


def test_patched_roots_are_inapplicable(tmp_path: Path) -> None:
    trace, oracle, fixture = _case(tmp_path, patched=True)
    result = conversion_ledger(trace, tmp_path, oracle, fixture)
    assert result["score"]["false_negatives"] == 0
    assert len(result["rows"]) == 5
    assert all(row == {"root": row["root"], "applicable": False} for row in result["rows"])
