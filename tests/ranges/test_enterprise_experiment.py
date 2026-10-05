"""Run Range B through real Compose, gateway evidence, validator, and score."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from offsecgym.evaluation import evaluate_run
from offsecgym.experiment import ScriptedExperimentRunner
from offsecgym.research.range_b_metrics import analyze_range_b
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.events import (
    ActionCompleted,
    FindingValidated,
    PrerequisiteBootstrapCompleted,
    RunCompleted,
)
from offsecgym.schemas.specs import BootstrapBudget, Budget, ExperimentSpec, RangeSpec


class MemoryEvents:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        sequence = sum(item.run_id == event.run_id for item in self.items) + 1
        stored = event.model_copy(update={"sequence_number": sequence})
        self.items.append(stored)
        return stored

    async def read_run(self, run_id: UUID):
        return [item for item in self.items if item.run_id == run_id]


@pytest.mark.docker
async def test_enterprise_vulnerable_patched_and_selective(tmp_path: Path) -> None:
    result = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required by CI: {result.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")
    runtime = ComposeRangeRuntime(tmp_path)
    events = MemoryEvents()
    runner = ScriptedExperimentRunner(runtime, events)
    base = RangeSpec.model_validate(
        yaml.safe_load(
            (Path(__file__).parents[2] / "examples" / "enterprise-change-control.yaml").read_text()
        )
    )
    variants = (
        base,
        base.model_copy(update={"patched": True}),
        base.model_copy(update={"patched_properties": ("B1-SOD",)}),
        base.model_copy(update={"patched_properties": ("B2-REVOKED-ROLE",)}),
        base.model_copy(update={"patched_properties": ("B3-CANCELLED-JOB",)}),
    )
    outcomes = []
    for variant in variants:
        outcome = await runner.run(
            ExperimentSpec(
                name="enterprise_scripted",
                seed=42,
                range=variant,
                budget=Budget(max_actions=180, max_http_requests=180, max_wall_seconds=360),
                bootstrap_budget=BootstrapBudget(
                    max_actions=40, max_http_requests=40, max_wall_seconds=90
                ),
                orchestrator="scripted",
                validation="deterministic",
            )
        )
        outcomes.append(outcome)
    vulnerable, patched, *selective = outcomes
    assert len({outcome.build_id for outcome in outcomes}) == 5
    assert all(outcome.evaluation.status == "completed" for outcome in outcomes)
    assert all(outcome.evaluation.score_valid for outcome in outcomes)
    assert (vulnerable.evaluation.true_positives, vulnerable.evaluation.false_positives) == (3, 0)
    assert vulnerable.evaluation.false_negatives == 0
    assert patched.evaluation.true_positives == patched.evaluation.false_positives == 0
    assert patched.evaluation.false_negatives == 0
    assert all(
        (item.evaluation.true_positives, item.evaluation.false_positives) == (2, 0)
        and item.evaluation.false_negatives == 0
        for item in selective
    )
    for outcome in outcomes:
        stream = await events.read_run(outcome.run_id)
        assert isinstance(stream[-1], RunCompleted)
        bootstrap = [event for event in stream if isinstance(event, PrerequisiteBootstrapCompleted)]
        assert len(bootstrap) == 1
        assert bootstrap[0].range_family == "enterprise_change_control_v1"
        assert bootstrap[0].action_count == bootstrap[0].http_request_count >= 18
        assert len([event for event in stream if isinstance(event, ActionCompleted)]) >= 20
        assert len([event for event in stream if isinstance(event, FindingValidated)]) == len(
            outcome.validations
        )
        oracle = StateOracleStore(runtime.state)._load_ground_truth(outcome.build_id)
        assert evaluate_run(outcome.findings, outcome.validations, oracle) == outcome.evaluation
        metrics = analyze_range_b(
            stream,
            outcome.evaluation,
            configured_opportunities=3,
            patched=outcome is patched,
        )
        assert metrics.transition_attempts >= 3
        assert metrics.successful_transitions >= 1
        assert metrics.proof_to_finding_conversion is None
        if outcome is vulnerable:
            assert metrics.root_recall == 1.0
            assert metrics.trace_proof_rate == 1.0
            assert metrics.patched_false_findings is None
        if outcome is patched:
            assert metrics.patched_false_findings == 0
