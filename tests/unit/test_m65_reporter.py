"""A reporter may retrieve closed evidence and submit findings, never probe."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_m65_witness_packet import _trace
from test_milestone_3_runner import MemoryEvents

from offsecgym.providers.base import ModelTurn, ModelUsage, ProviderFailure
from offsecgym.research.m65_reporter import ReadOnlyReporter, reporter_tools
from offsecgym.research.m65_witness_packet import ReadOnlyReporterTools, build_reporter_bundle
from offsecgym.schemas.domain import CandidateFinding
from offsecgym.schemas.events import ModelCallCompleted, ModelCallFailed, ModelCallStarted
from offsecgym.schemas.specs import Budget, ModelSpec


def _call(name: str, arguments: dict[str, object], index: int) -> dict[str, object]:
    return {
        "type": "function_call",
        "name": name,
        "call_id": f"call-{index}",
        "arguments": json.dumps(arguments),
    }


class QueueProvider:
    def __init__(self, outputs: list[tuple[dict[str, object], ...] | Exception]) -> None:
        self.outputs = outputs
        self.requests: list[dict[str, object]] = []

    def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
        payload = {
            "model": model.name,
            "instructions": instructions,
            "input": input_items,
            "tools": tools,
            "max_output_tokens": max_output_tokens,
        }
        self.requests.append(payload)
        return payload

    async def complete(self, request_payload):
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return ModelTurn(
            response_id=f"response-{len(self.requests)}",
            status="completed",
            output=output,
            usage=ModelUsage(input_tokens=200, output_tokens=100),
            raw_response={"output": output},
        )


def _reporter(tmp_path: Path, provider: QueueProvider, events: MemoryEvents):
    return ReadOnlyReporter(provider, ModelSpec(provider="fake", name="fake"), events, tmp_path)


def _budget() -> Budget:
    return Budget(max_total_tokens=30_000, max_model_calls=3, max_output_tokens_per_call=512)


@pytest.mark.asyncio
async def test_reporter_uses_only_lookup_and_bound_submission(tmp_path: Path) -> None:
    run_id, trace, first, target, _, _ = _trace(tmp_path)
    bundle = build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    evidence = {
        "action_id": str(first[0].action_id),
        "evidence_id": str(first[2]),
        "description": "cross-tenant response",
    }
    finding_args = {
        "claim": "A member read a foreign workspace document",
        "asset_id": str(target),
        "evidence": [evidence],
        "root_cause_hypothesis": "missing tenant boundary check",
        "subject_role": "member",
        "action": "GET /api/documents/{id}",
        "resource_type": "document",
        "object_relation": "foreign_workspace",
        "expected": "deny",
    }
    provider = QueueProvider(
        [
            (_call("get_action_evidence", {"action_id": str(first[0].action_id)}, 1),),
            (_call("submit_authorization_finding", finding_args, 2),),
            (_call("finish_report", {"summary": "Done"}, 3),),
        ]
    )
    events = MemoryEvents()
    sink = AsyncMock()

    async def submit(proposal):
        return CandidateFinding(
            **proposal.model_dump(mode="python"),
            finding_id=uuid4(),
            run_id=run_id,
            range_instance_id=bundle.packet.range_instance_id,
            range_generation=bundle.packet.range_generation,
        )

    sink.submit.side_effect = submit
    result = await _reporter(tmp_path, provider, events).run(
        bundle,
        ReadOnlyReporterTools(bundle, sink),
        reporter_budget=_budget(),
        global_budget=_budget(),
    )
    assert result.status == "completed"
    assert len(result.submitted_finding_ids) == 1
    assert result.evidence_lookup_action_ids == (first[0].action_id,)
    assert sink.submit.await_count == 1
    assert "http_request" not in {tool["name"] for tool in reporter_tools()}
    assert {tool["name"] for tool in provider.requests[0]["tools"]} == {
        "get_action_evidence",
        "submit_authorization_finding",
        "submit_exposure_finding",
        "submit_transition_finding",
        "finish_report",
    }
    assert len([item for item in events.items if isinstance(item, ModelCallStarted)]) == 3
    assert len([item for item in events.items if isinstance(item, ModelCallCompleted)]) == 3


@pytest.mark.asyncio
async def test_reporter_provider_failure_keeps_error_artifact(tmp_path: Path) -> None:
    run_id, trace, _, _, _, _ = _trace(tmp_path)
    bundle = build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    provider = QueueProvider(
        [ProviderFailure("provider_rate_limited", 429, {"error": "rate limited"})]
    )
    events = MemoryEvents()
    with pytest.raises(ProviderFailure, match="provider_rate_limited"):
        await _reporter(tmp_path, provider, events).run(
            bundle,
            ReadOnlyReporterTools(bundle, AsyncMock()),
            reporter_budget=_budget(),
            global_budget=_budget(),
        )
    failed = [item for item in events.items if isinstance(item, ModelCallFailed)]
    assert len(failed) == 1
    assert failed[0].http_status == 429
    assert failed[0].response_artifact_id is not None


@pytest.mark.asyncio
async def test_reporter_rejects_foreign_lookup_without_sink_call(tmp_path: Path) -> None:
    run_id, trace, _, _, _, _ = _trace(tmp_path)
    bundle = build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    provider = QueueProvider([(_call("get_action_evidence", {"action_id": str(uuid4())}, 1),)])
    sink = AsyncMock()
    result = await _reporter(tmp_path, provider, MemoryEvents()).run(
        bundle,
        ReadOnlyReporterTools(bundle, sink),
        reporter_budget=Budget(
            max_total_tokens=30_000, max_model_calls=1, max_output_tokens_per_call=512
        ),
        global_budget=_budget(),
    )
    assert result.status == "budget_exhausted"
    sink.submit.assert_not_awaited()
