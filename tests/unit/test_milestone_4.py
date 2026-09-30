"""Provider, model-turn, and scoring contracts for the first monolithic agent."""

from __future__ import annotations

import asyncio
import json
import stat
from urllib.error import HTTPError

import pytest
from test_milestone_3_runner import MemoryEvents, no_docker_runtime, scripted_spec

from offsecgym.evaluation import unscored_run
from offsecgym.experiment import MonolithicExperimentRunner
from offsecgym.providers.artifacts import ModelCallArtifacts
from offsecgym.providers.base import ModelTurn, ProviderFailure, ProviderRequestError
from offsecgym.providers.openai import OpenAIResponsesProvider
from offsecgym.schemas.events import (
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    ModelToolRejected,
    RunCompleted,
)
from offsecgym.schemas.specs import Budget, ModelSpec
from offsecgym.solver.monolithic import model_tools


def turn(*output: dict[str, object], input_tokens: int = 20, output_tokens: int = 10) -> ModelTurn:
    raw_response = {
        "id": "resp_mock",
        "status": "completed",
        "output": list(output),
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    return ModelTurn(
        response_id="resp_mock",
        status="completed",
        output=output,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
        raw_response=raw_response,
    )


def model_spec(*, tokens: int = 500, calls: int = 4):
    base = scripted_spec()
    return base.model_copy(
        update={
            "orchestrator": "monolithic",
            "memory": "transcript",
            "surface_visibility": "known_routes",
            "model": ModelSpec(
                provider="openai",
                name="mock-model",
                input_usd_per_million_tokens=1,
                output_usd_per_million_tokens=2,
            ),
            "budget": Budget(max_actions=5, max_total_tokens=tokens, max_model_calls=calls),
        }
    )


class QueueProvider:
    def __init__(self, *responses: ModelTurn | Exception) -> None:
        self.responses = list(responses)
        self.requests = []

    def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
        return {
            "model": model.name,
            "instructions": instructions,
            "input": list(input_items),
            "tools": tools,
            "max_output_tokens": max_output_tokens,
        }

    async def complete(self, request_payload):
        self.requests.append(request_payload)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_monolithic_runner_rejects_unimplemented_memory_mode(tmp_path, monkeypatch) -> None:
    spec = model_spec().model_copy(update={"memory": "structured"})
    with pytest.raises(ValueError, match="supported deterministic SaaS"):
        await MonolithicExperimentRunner(
            no_docker_runtime(tmp_path, monkeypatch), MemoryEvents(), QueueProvider()
        ).run(spec)


@pytest.mark.asyncio
async def test_provider_failure_is_terminal_and_unscored(tmp_path, monkeypatch) -> None:
    events = MemoryEvents()
    provider = QueueProvider(ProviderFailure("provider_server_error", 503))
    outcome = await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), events, provider
    ).run(model_spec())
    assert outcome.evaluation.status == "provider_failed"
    assert outcome.evaluation.score_valid is False
    assert outcome.evaluation.false_negatives is None
    assert outcome.failure_reason == "provider_server_error"
    assert len([item for item in events.items if isinstance(item, ModelCallStarted)]) == 1
    failures = [item for item in events.items if isinstance(item, ModelCallFailed)]
    assert len(failures) == 1 and failures[0].http_status == 503
    assert events.items[-1].status == "provider_failed"


@pytest.mark.asyncio
async def test_failed_provider_response_is_retained(tmp_path, monkeypatch) -> None:
    events = MemoryEvents()
    runtime = no_docker_runtime(tmp_path, monkeypatch)
    raw_response = {"id": "resp_failed", "status": "failed", "error": {"code": "upstream"}}
    provider = QueueProvider(ProviderFailure("provider_response_failed", raw_response=raw_response))
    outcome = await MonolithicExperimentRunner(runtime, events, provider).run(model_spec())
    assert outcome.evaluation.status == "provider_failed"
    started = next(item for item in events.items if isinstance(item, ModelCallStarted))
    failed = next(item for item in events.items if isinstance(item, ModelCallFailed))
    assert (
        ModelCallArtifacts(runtime.state.root).read_verified(
            outcome.run_id,
            started.call_id,
            "response",
            failed.response_artifact_id,
            failed.response_sha256,
        )
        == raw_response
    )


@pytest.mark.asyncio
async def test_response_artifact_failure_is_unscored_and_closes_call(tmp_path, monkeypatch) -> None:
    original_write = ModelCallArtifacts.write

    def fail_response(self, run_id, call_id, kind, payload):
        if kind == "response":
            raise OSError("disk full")
        return original_write(self, run_id, call_id, kind, payload)

    monkeypatch.setattr(ModelCallArtifacts, "write", fail_response)
    events = MemoryEvents()
    outcome = await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), events, QueueProvider(turn())
    ).run(model_spec())
    assert outcome.evaluation.status == "environment_failed"
    failed = [item for item in events.items if isinstance(item, ModelCallFailed)]
    assert len(failed) == 1
    assert failed[0].reason_code == "model_response_artifact_failed"


@pytest.mark.asyncio
async def test_subminimum_remaining_budget_skips_provider(tmp_path, monkeypatch) -> None:
    events = MemoryEvents()
    provider = QueueProvider()
    outcome = await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), events, provider
    ).run(model_spec(tokens=15))
    assert outcome.evaluation.status == "budget_exhausted"
    assert outcome.failure_reason == "model_token_budget_exhausted"
    assert provider.requests == []
    assert not any(isinstance(item, ModelCallStarted) for item in events.items)


@pytest.mark.asyncio
async def test_model_usage_and_agent_budget_are_scored(tmp_path, monkeypatch) -> None:
    events = MemoryEvents()
    provider = QueueProvider(turn(input_tokens=9, output_tokens=7))
    outcome = await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), events, provider
    ).run(model_spec(tokens=16))
    assert outcome.evaluation.status == "budget_exhausted"
    assert outcome.evaluation.score_valid is True
    assert outcome.evaluation.false_negatives == 5
    completed = [item for item in events.items if isinstance(item, ModelCallCompleted)]
    assert len(completed) == 1
    assert (completed[0].input_tokens, completed[0].output_tokens) == (9, 7)
    assert completed[0].estimated_cost_usd == pytest.approx(0.000023)


@pytest.mark.asyncio
async def test_malformed_tool_calls_are_scored_agent_failure(tmp_path, monkeypatch) -> None:
    events = MemoryEvents()
    output = tuple(
        {
            "type": "function_call",
            "call_id": f"call_{index}",
            "name": "http_request",
            "arguments": "{bad-json",
        }
        for index in range(3)
    )
    outcome = await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), events, QueueProvider(turn(*output))
    ).run(model_spec())
    assert outcome.evaluation.status == "agent_failed"
    assert outcome.evaluation.score_valid is True
    assert outcome.evaluation.false_negatives == 5
    assert len([item for item in events.items if isinstance(item, ModelToolRejected)]) == 3
    assert isinstance(events.items[-1], RunCompleted)


@pytest.mark.asyncio
async def test_model_artifacts_preserve_exact_turns_and_next_request(tmp_path, monkeypatch) -> None:
    events = MemoryEvents()
    runtime = no_docker_runtime(tmp_path, monkeypatch)
    reasoning = {"type": "reasoning", "encrypted_content": "opaque-state"}
    bad_call = {
        "type": "function_call",
        "call_id": "bad_call",
        "name": "http_request",
        "arguments": "{bad-json",
    }
    provider = QueueProvider(turn(reasoning, bad_call), turn())
    outcome = await MonolithicExperimentRunner(runtime, events, provider).run(model_spec())
    assert outcome.evaluation.status == "completed"
    starts = [item for item in events.items if isinstance(item, ModelCallStarted)]
    completions = [item for item in events.items if isinstance(item, ModelCallCompleted)]
    assert len(starts) == len(completions) == 2
    artifacts = ModelCallArtifacts(runtime.state.root)
    for started, completed in zip(starts, completions, strict=True):
        request = artifacts.read_verified(
            outcome.run_id,
            started.call_id,
            "request",
            started.request_artifact_id,
            started.request_sha256,
        )
        response = artifacts.read_verified(
            outcome.run_id,
            started.call_id,
            "response",
            completed.response_artifact_id,
            completed.response_sha256,
        )
        assert request == provider.requests.pop(0)
        assert response["output"] == (list((reasoning, bad_call)) if started == starts[0] else [])
        for kind in ("request", "response"):
            path = (
                runtime.state.root
                / "model_calls"
                / outcome.run_id.hex
                / started.call_id.hex
                / f"{kind}.json"
            )
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert "test-secret" not in path.read_text()
    second_request = artifacts.read_verified(
        outcome.run_id,
        starts[1].call_id,
        "request",
        starts[1].request_artifact_id,
        starts[1].request_sha256,
    )
    assert reasoning in second_request["input"]
    assert bad_call in second_request["input"]
    assert second_request["input"][-1]["type"] == "function_call_output"
    assert json.loads(second_request["input"][-1]["output"])["error"] == "invalid_tool_call"
    path = (
        runtime.state.root
        / "model_calls"
        / outcome.run_id.hex
        / starts[0].call_id.hex
        / "response.json"
    )
    tampered = json.loads(path.read_text())
    tampered["payload"]["output"][1]["arguments"] = "{}"
    path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="digest mismatch"):
        artifacts.read_verified(
            outcome.run_id,
            starts[0].call_id,
            "response",
            completions[0].response_artifact_id,
            completions[0].response_sha256,
        )


@pytest.mark.asyncio
async def test_explicit_per_call_output_limit(tmp_path, monkeypatch) -> None:
    provider = QueueProvider(turn())
    spec = model_spec(tokens=30000).model_copy(
        update={
            "budget": Budget(
                max_actions=5, max_total_tokens=30000, max_output_tokens_per_call=16000
            )
        }
    )
    await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), MemoryEvents(), provider
    ).run(spec)
    assert provider.requests[0]["max_output_tokens"] == 16000


@pytest.mark.asyncio
async def test_monolithic_rejects_unimplemented_surface_visibility(tmp_path, monkeypatch) -> None:
    spec = model_spec().model_copy(update={"surface_visibility": "black_box"})
    with pytest.raises(ValueError, match="supported deterministic SaaS"):
        await MonolithicExperimentRunner(
            no_docker_runtime(tmp_path, monkeypatch), MemoryEvents(), QueueProvider()
        ).run(spec)


@pytest.mark.asyncio
async def test_cancelled_model_call_closes_call_and_run(tmp_path, monkeypatch) -> None:
    class CancelledProvider:
        prepare_request = QueueProvider.prepare_request

        async def complete(self, request_payload):
            raise asyncio.CancelledError

    events = MemoryEvents()
    with pytest.raises(asyncio.CancelledError):
        await MonolithicExperimentRunner(
            no_docker_runtime(tmp_path, monkeypatch), events, CancelledProvider()
        ).run(model_spec())
    failed = [item for item in events.items if isinstance(item, ModelCallFailed)]
    assert len(failed) == 1 and failed[0].reason_code == "model_call_cancelled"
    assert isinstance(events.items[-1], RunCompleted)
    assert events.items[-1].status == "cancelled"


def test_provider_adapter_uses_stateless_strict_tool_contract(monkeypatch) -> None:
    captured = {}
    adapter = OpenAIResponsesProvider("test-secret")

    def fake_post(payload):
        captured.update(payload)
        return {
            "id": "resp_1",
            "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": []}],
            "usage": {"input_tokens": 12, "output_tokens": 4},
        }

    monkeypatch.setattr(adapter, "_post", fake_post)
    import asyncio

    result = asyncio.run(
        adapter.complete(
            adapter.prepare_request(
                ModelSpec(provider="openai", name="mock-model"),
                "synthetic only",
                [{"role": "user", "content": "hello"}],
                model_tools(),
                128,
            )
        )
    )
    assert result.usage.input_tokens == 12
    assert result.raw_response["id"] == "resp_1"
    assert captured["store"] is False
    assert captured["parallel_tool_calls"] is False
    assert captured["include"] == ["reasoning.encrypted_content"]
    assert all(item["strict"] is True for item in captured["tools"])
    assert "test-secret" not in json.dumps(captured)


@pytest.mark.parametrize(
    ("http_status", "expected_type", "reason"),
    (
        (401, ProviderFailure, "provider_auth_failed"),
        (408, ProviderFailure, "provider_timeout"),
        (429, ProviderFailure, "provider_rate_limited"),
        (503, ProviderFailure, "provider_server_error"),
        (400, ProviderRequestError, "provider_request_rejected"),
    ),
)
def test_openai_http_status_classification(monkeypatch, http_status, expected_type, reason) -> None:
    def broken_open(*args, **kwargs):
        raise HTTPError("https://api.openai.com/v1/responses", http_status, "error", {}, None)

    adapter = OpenAIResponsesProvider("test-secret")
    monkeypatch.setattr(adapter._opener, "open", broken_open)
    with pytest.raises(expected_type) as error:
        adapter._post({"model": "mock"})
    assert error.value.reason_code == reason
    assert "test-secret" not in str(error.value)


def test_cost_budget_needs_explicit_prices_and_new_unscored_states() -> None:
    spec = model_spec()
    with pytest.raises(ValueError, match="cost budget requires"):
        type(spec).model_validate(
            {
                **spec.model_dump(mode="python"),
                "model": {"provider": "openai", "name": "mock-model"},
                "budget": {"max_cost_usd": 0.01, "max_total_tokens": 500},
            }
        )
    assert unscored_run("provider_failed").score_valid is False
    assert unscored_run("cancelled").false_negatives is None
