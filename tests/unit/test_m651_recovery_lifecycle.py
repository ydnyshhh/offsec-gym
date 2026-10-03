"""Fake reporter recovers a missed root from closed probe evidence."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_m65_reporter import QueueProvider, _call
from test_milestone_3_validator import document_case

from offsecgym.experiment.reporter_recovery import ReporterRecoveryRunner, combined_budget
from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.research.m65_conversion_ledger import conversion_ledger
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.events import (
    FindingSubmitted,
    FindingValidated,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.specs import Budget, ModelSpec, ReporterBudget


async def _exercise(tmp_path: Path, *, patched: bool = False, integrated: bool = False):
    validator, example, context, oracle, events, _ = await document_case(tmp_path, patched=patched)
    probe = [
        RunStarted(
            run_id=context.run_id, actor="controller", sequence_number=1, experiment_hash="0" * 64
        ),
        RangeStarted(
            run_id=context.run_id,
            actor="controller",
            sequence_number=2,
            build_id=context.build_id,
            range_instance_id=context.range_instance_id,
            range_generation=0,
        ),
        *[
            item.model_copy(update={"sequence_number": i})
            for i, item in enumerate(events.items, start=3)
        ],
    ]
    if integrated:
        probe.append(
            FindingSubmitted(
                run_id=context.run_id,
                actor="agent",
                sequence_number=len(probe) + 1,
                finding=example,
            )
        )
    events.items = probe
    arg = {
        "claim": example.claim,
        "asset_id": str(example.asset_id),
        "evidence": [ref.model_dump(mode="json") for ref in example.evidence],
        "root_cause_hypothesis": "missing tenant boundary check",
        **example.security_property.model_dump(mode="json", exclude={"kind"}),
    }
    provider = QueueProvider(
        [
            (_call("submit_authorization_finding", arg, 1),),
            (_call("finish_report", {"summary": "complete"}, 2),),
        ]
    )
    reporter_budget = ReporterBudget(
        max_total_tokens=30_000,
        max_model_calls=4,
        max_output_tokens_per_call=4096,
        max_wall_seconds=300,
        max_retrieval_calls=24,
        max_finding_submissions=12,
    )
    runner = ReporterRecoveryRunner(
        SimpleNamespace(state=SimpleNamespace(root=tmp_path)),
        events,
        provider,
        reporter_budget,
    )
    experiment = ExperimentContext(
        run_id=context.run_id,
        range_instance_id=context.range_instance_id,
        range_generation=0,
        budget=combined_budget(
            Budget(max_total_tokens=120_000, max_model_calls=20), reporter_budget
        ),
    )
    sink = BoundFindingSink(events, experiment)
    spec = SimpleNamespace(model=ModelSpec(provider="fake", name="fake"))
    await runner._after_agent(spec, experiment, sink, (example,) if integrated else (), "completed")
    findings = [e.finding for e in events.items if isinstance(e, FindingSubmitted)]
    assert len(findings) == (2 if integrated else 1)
    for finding in findings:
        verdict = await validator.validate(finding, context)
        await events.append(
            FindingValidated(run_id=context.run_id, actor="validator", result=verdict)
        )
    await events.append(RunCompleted(run_id=context.run_id, actor="controller", status="completed"))
    fixture = json.loads((validator.state.build_dir(context.build_id) / "fixture.json").read_text())
    ledger = conversion_ledger(events.items, tmp_path, oracle, fixture)
    return ledger


@pytest.mark.asyncio
async def test_reporter_recovers_proof_left_without_integrated_finding(tmp_path: Path) -> None:
    ledger = await _exercise(tmp_path)
    assert ledger["integrated_distinct_roots"] == 0
    assert ledger["incremental_reporter_roots"] == 1
    doc = next(row for row in ledger["rows"] if row["root"] == "DOC-CROSS-TENANT-READ")
    assert doc["complete_trace_proof"]
    assert doc["recoverable_missed_root"]
    assert doc["reporter_recovered_root"]
    assert ledger["probe_model_calls"] == 0
    assert ledger["reporter_model_calls"] == 2


@pytest.mark.asyncio
async def test_reporter_duplicate_is_not_incremental_recovery(tmp_path: Path) -> None:
    ledger = await _exercise(tmp_path, integrated=True)
    assert ledger["integrated_distinct_roots"] == 1
    assert ledger["reporter_distinct_roots"] == 1
    assert ledger["incremental_reporter_roots"] == 0
    assert ledger["reporter_duplicate_validated_submissions"] == 1


@pytest.mark.asyncio
async def test_patched_reporter_claim_is_rejected(tmp_path: Path) -> None:
    ledger = await _exercise(tmp_path, patched=True)
    assert ledger["incremental_reporter_roots"] == 0
    assert ledger["reporter_rejected"] == 1
    assert all(not row["applicable"] for row in ledger["rows"])
