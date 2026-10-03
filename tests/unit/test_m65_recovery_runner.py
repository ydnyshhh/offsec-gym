"""The prospective reporter runs after probing and before ordinary validation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_m65_reporter import QueueProvider, _call
from test_m65_witness_packet import _trace
from test_milestone_3_runner import MemoryEvents, scripted_spec

from offsecgym.experiment.reporter_recovery import ReporterRecoveryRunner, combined_budget
from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.providers.base import ProviderFailure
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.events import (
    ActionRequested,
    ModelCallCompleted,
    ReporterFinished,
    ReporterStarted,
    parse_event,
)
from offsecgym.schemas.specs import Budget, ModelSpec


def _probe_spec():
    return scripted_spec().model_copy(update={"model": ModelSpec(provider="fake", name="fake")})


def _reporter_budget() -> Budget:
    return Budget(
        max_total_tokens=30_000,
        max_model_calls=2,
        max_output_tokens_per_call=512,
        max_wall_seconds=30,
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
