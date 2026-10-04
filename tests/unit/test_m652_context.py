"""The new comparison preserves a genuine post-tool carry and equal reporter tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_m65_reporter import QueueProvider, _budget, _call, _reporter
from test_m65_witness_packet import _trace
from test_milestone_3_runner import MemoryEvents

from offsecgym.experiment.reporting_context import PairedReportingContextRunner
from offsecgym.providers.artifacts import ModelCallArtifacts
from offsecgym.providers.openai import request_wire_bytes
from offsecgym.research.m65_witness_packet import ReadOnlyReporterTools, build_reporter_bundle
from offsecgym.research.m652_checkpoint import ProbeCheckpointStore
from offsecgym.schemas.events import (
    ModelCallCompleted,
    ModelCallStarted,
    ProbeCheckpointSaved,
    RunStarted,
    parse_event,
)


@pytest.mark.asyncio
async def test_checkpoint_round_trip_binds_exact_prefix_and_private_carry(tmp_path: Path) -> None:
    run_id, call_id = uuid4(), uuid4()
    events = MemoryEvents()
    await events.append(RunStarted(run_id=run_id, actor="test", experiment_hash="a" * 64))
    artifacts = ModelCallArtifacts(tmp_path)
    request_id, request_sha = artifacts.write(run_id, call_id, "request", {"input": "probe"})
    output = [
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"type": "function_call", "call_id": "c1", "name": "http_request", "arguments": "{}"},
    ]
    response_id, response_sha = artifacts.write(run_id, call_id, "response", {"output": output})
    await events.append(
        ModelCallStarted(
            run_id=run_id,
            actor="test",
            call_id=call_id,
            provider="fake",
            model="fake",
            input_sha256=request_sha,
            request_artifact_id=request_id,
            request_sha256=request_sha,
        )
    )
    await events.append(
        ModelCallCompleted(
            run_id=run_id,
            actor="test",
            call_id=call_id,
            provider_status="completed",
            tool_call_count=1,
            input_tokens=10,
            output_tokens=5,
            response_artifact_id=response_id,
            response_sha256=response_sha,
        )
    )
    store = ProbeCheckpointStore(events, tmp_path)
    saved = await store.save(
        run_id,
        latest_model_call_id=call_id,
        latest_request_sha256=request_sha,
        base_items=[{"role": "user", "content": "probe"}],
        carry_items=[
            *output,
            {"type": "function_call_output", "call_id": "c1", "output": "{}"},
        ],
        working_state_text="recent checked action",
    )
    assert isinstance(parse_event(saved.model_dump(mode="python")), ProbeCheckpointSaved)
    checkpoint = await store.load_latest(run_id)
    assert checkpoint.source_sequence == 3
    assert checkpoint.carry_items[0]["encrypted_content"] == "opaque"
    assert PairedReportingContextRunner._continuation_items(checkpoint) == (
        {"role": "user", "content": "probe"},
        *checkpoint.carry_items,
        {"role": "user", "content": "recent checked action"},
    )
    path = store._path(run_id, checkpoint.checkpoint_id)
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text(path.read_text().replace("opaque", "changed"))
    with pytest.raises(ValueError, match="digest"):
        await store.load_latest(run_id)


@pytest.mark.asyncio
async def test_fresh_and_continuation_reporters_share_tools_and_evidence(tmp_path: Path) -> None:
    run_id, trace, *_ = _trace(tmp_path)
    bundle = build_reporter_bundle(trace, tmp_path, expected_run_id=run_id)
    context = (
        {"role": "user", "content": "probe goal"},
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"role": "user", "content": "checked entity"},
    )
    payloads = []
    for items in ((), context):
        events = MemoryEvents()
        provider = QueueProvider([(_call("finish_report", {"summary": "done"}, 1),)])
        reporter = _reporter(tmp_path, provider, events)
        result = await reporter.run(
            bundle,
            ReadOnlyReporterTools(bundle, AsyncMock()),
            reporter_budget=_budget(),
            global_budget=_budget(),
            initial_context_items=items,
        )
        assert result.status == "completed"
        payloads.append(provider.requests[0])
    assert payloads[0]["instructions"] == payloads[1]["instructions"]
    assert payloads[0]["tools"] == payloads[1]["tools"]
    assert payloads[0]["max_output_tokens"] == payloads[1]["max_output_tokens"]
    assert payloads[0]["input"][-1] == payloads[1]["input"][-1]
    assert payloads[0]["input"] != payloads[1]["input"]
    assert "http_request" not in {tool["name"] for tool in payloads[0]["tools"]}


def test_model_artifacts_have_separate_branch_namespaces(tmp_path: Path) -> None:
    run_id, call_id, first, second = (uuid4() for _ in range(4))
    left = ModelCallArtifacts(tmp_path, branch_id=first)
    right = ModelCallArtifacts(tmp_path, branch_id=second)
    left_id, left_sha = left.write(run_id, call_id, "request", {"input": "left"})
    right_id, right_sha = right.write(run_id, call_id, "request", {"input": "right"})
    assert left_sha != right_sha
    assert left.read_verified(run_id, call_id, "request", left_id, left_sha) == {"input": "left"}
    assert right.read_verified(run_id, call_id, "request", right_id, right_sha) == {
        "input": "right"
    }
    third_call_id = uuid4()
    ordered_request = {"model": "fake", "instructions": "report", "input": []}
    third_id, third_sha = left.write(run_id, third_call_id, "request", ordered_request)
    reconstructed = left.read_verified(run_id, third_call_id, "request", third_id, third_sha)
    assert request_wire_bytes(reconstructed) == request_wire_bytes(ordered_request)
