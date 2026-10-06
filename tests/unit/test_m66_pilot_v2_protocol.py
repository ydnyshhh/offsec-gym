"""Freeze the corrected M6.6 feasibility pilot without model execution."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from offsecgym.research import m66_pilot_protocol_v2 as protocol
from offsecgym.runtime.enterprise import FAMILY as ENTERPRISE_FAMILY
from offsecgym.runtime.enterprise import fixture_for_seed
from offsecgym.schemas.events import FindingValidated, ReporterStarted, RunCompleted
from offsecgym.schemas.specs import ExperimentSpec

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))
from m66_extract_stages import _witness_source_trace  # noqa: E402


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def test_v2_selection_and_bootstrap_budget_are_frozen() -> None:
    manifest = protocol.plan_pilot(
        ROOT,
        source_commit="950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d",
        protocol_commit=_head(),
        price_checked_at="2026-10-06T12:00:00Z",
    )
    assert manifest["protocol"] == "m66-pilot-v2"
    assert manifest["planned_trajectories"] == 8
    assert manifest["paid_model_calls_authorized"] is False
    assert manifest["seed_selection"]["prior_exclusion_count"] == 160
    assert {cell["seed"] for cell in manifest["cells"]} == {712868, 551180}
    assert len({cell["cell_id"] for cell in manifest["cells"]}) == 8
    assert {cell["range_family"] for cell in manifest["cells"]} == {
        "saas",
        ENTERPRISE_FAMILY,
    }
    for family, entry in manifest["configs"].items():
        spec = ExperimentSpec.model_validate(yaml.safe_load((ROOT / entry["path"]).read_text()))
        assert spec.bootstrap_budget is not None
        assert spec.bootstrap_budget.max_actions == (32 if family == "saas" else 40)
        assert spec.bootstrap_budget.max_http_requests == spec.bootstrap_budget.max_actions
        assert spec.model is not None
        assert spec.model.input_usd_per_million_tokens == 3.0
        assert spec.model.output_usd_per_million_tokens == 15.0
    fixture, _ = fixture_for_seed(551180)
    required_bootstrap_gets = len(fixture["users"]) + 5 * len(fixture["organizations"])
    assert required_bootstrap_gets == 33
    assert required_bootstrap_gets < 40


def test_completed_trace_events_are_excluded_from_witness_source() -> None:
    keep = object()
    trace = [
        keep,
        FindingValidated.model_construct(),
        ReporterStarted.model_construct(),
        RunCompleted.model_construct(),
    ]
    assert _witness_source_trace(trace) == [keep]
