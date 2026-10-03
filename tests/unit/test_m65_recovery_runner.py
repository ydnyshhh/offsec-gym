"""The prospective reporter runs after probing and before ordinary validation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_m65_reporter import QueueProvider, _call
from test_m65_witness_packet import _trace
from test_milestone_3_runner import MemoryEvents, no_docker_runtime, scripted_spec

from offsecgym.experiment.reporter_recovery import ReporterRecoveryRunner, combined_budget
from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.providers.base import ProviderFailure
from offsecgym.schemas.domain import AgentResult, ExperimentContext
from offsecgym.schemas.events import (
    ActionRequested,
    ModelCallCompleted,
    ReporterFinished,
    ReporterStarted,
    parse_event,
)
from offsecgym.schemas.specs import BootstrapBudget, Budget, ModelSpec, ReporterBudget


def _probe_spec():
    return scripted_spec().model_copy(update={"model": ModelSpec(provider="fake", name="fake")})


def _reporter_budget() -> ReporterBudget:
    return ReporterBudget(
        max_total_tokens=30_000,
        max_model_calls=2,
        max_output_tokens_per_call=512,
        max_wall_seconds=30,
        max_retrieval_calls=8,
        max_finding_submissions=4,
    )


def test_combined_budget_preserves_nonmodel_probe_caps() -> None:
    probe = Budget(
        max_total_tokens=120_000,
        max_model_calls=20,
        max_actions=60,
        max_http_requests=60,
    )
    combined = combined_budget(probe, _reporter_budget())
    assert combined.max_total_tokens == 150_000
    assert combined.max_model_calls == 22
    assert combined.max_wall_seconds == _reporter_budget().max_wall_seconds
    assert combined.max_actions == probe.max_actions
    assert combined.max_http_requests == probe.max_http_requests
    with pytest.raises(ValueError, match="both be set"):
        combined_budget(probe, _reporter_budget().model_copy(update={"max_cost_usd": 1.0}))


@pytest.mark.asyncio
async def test_recovery_boundary_has_no_post_probe_http_and_replays_typed(tmp_path: Path) -> None:
    run_id, trace, _, _, _, _ = _trace(tmp_path)
    events = MemoryEvents()
    events.items = trace.copy()
    provider = QueueProvider([(_call("finish_report", {"summary": "No new claim"}, 1),)])
    runner = ReporterRecoveryRunner(
        SimpleNamespace(state=SimpleNamespace(root=tmp_path)),
        events,
        provider,
        _reporter_budget(),
    )
    context = ExperimentContext(
        run_id=run_id,
        range_instance_id=trace[1].range_instance_id,
        range_generation=0,
        budget=combined_budget(
            Budget(max_total_tokens=120_000, max_model_calls=20), _reporter_budget()
        ),
    )
    await runner._after_agent(
        _probe_spec(), context, BoundFindingSink(events, context), (), "completed"
    )
    starts = [item for item in events.items if isinstance(item, ReporterStarted)]
    finishes = [item for item in events.items if isinstance(item, ReporterFinished)]
    assert len(starts) == len(finishes) == 1
    assert starts[0].sequence_number < finishes[0].sequence_number
    assert finishes[0].status == "completed"
    assert not any(
        isinstance(item, ActionRequested) and item.sequence_number > starts[0].sequence_number
        for item in events.items
    )
    assert len([item for item in events.items if isinstance(item, ModelCallCompleted)]) == 1
    assert parse_event(finishes[0].model_dump(mode="python")) == finishes[0]


@pytest.mark.asyncio
async def test_provider_failure_is_reporter_status_not_probe_failure(tmp_path: Path) -> None:
    run_id, trace, _, _, _, _ = _trace(tmp_path)
    events = MemoryEvents()
    events.items = trace.copy()
    runner = ReporterRecoveryRunner(
        SimpleNamespace(state=SimpleNamespace(root=tmp_path)),
        events,
        QueueProvider([ProviderFailure("provider_rate_limited", 429)]),
        _reporter_budget(),
    )
    context = ExperimentContext(
        run_id=run_id,
        range_instance_id=trace[1].range_instance_id,
        range_generation=0,
        budget=combined_budget(
            Budget(max_total_tokens=120_000, max_model_calls=20), _reporter_budget()
        ),
    )
    await runner._after_agent(
        _probe_spec(), context, BoundFindingSink(events, context), (), "budget_exhausted"
    )
    finished = next(item for item in events.items if isinstance(item, ReporterFinished))
    assert finished.status == "provider_failed"
    assert finished.reason_code == "provider_rate_limited"


@pytest.mark.asyncio
async def test_full_recovery_lifecycle_scores_after_reporter(tmp_path: Path, monkeypatch) -> None:
    class NoOpProbe:
        async def run(self, task, context, tools):
            return AgentResult(task_id=task.task_id, status="completed")

    class NoOpRecoveryRunner(ReporterRecoveryRunner):
        async def _before_agent(self, spec, context, tools):
            return None

        def _agent(self, findings_store, spec):
            return NoOpProbe()

    spec = _probe_spec().model_copy(
        update={
            "orchestrator": "bootstrapped_monolithic",
            "memory": "structured",
            "surface_visibility": "known_routes",
            "bootstrap_budget": BootstrapBudget(
                max_actions=32, max_http_requests=32, max_wall_seconds=120
            ),
            "budget": Budget(
                max_total_tokens=120_000,
                max_model_calls=20,
                max_actions=60,
                max_http_requests=60,
                max_workers=6,
                max_concurrency=1,
            ),
        }
    )
    events = MemoryEvents()
    runner = NoOpRecoveryRunner(
        no_docker_runtime(tmp_path, monkeypatch),
        events,
        QueueProvider([(_call("finish_report", {"summary": "No findings"}, 1),)]),
        _reporter_budget(),
    )
    outcome = await runner.run(spec)
    assert outcome.evaluation.score_valid
    assert outcome.evaluation.false_negatives == 5
    assert outcome.findings == ()
    assert len([item for item in events.items if isinstance(item, ReporterStarted)]) == 1
    assert len([item for item in events.items if isinstance(item, ReporterFinished)]) == 1
