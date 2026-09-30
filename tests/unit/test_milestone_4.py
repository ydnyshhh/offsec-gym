"""Provider, model-turn, and scoring contracts for the first monolithic agent."""

from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError

import pytest
from test_milestone_3_runner import MemoryEvents, no_docker_runtime, scripted_spec

from offsecgym.evaluation import unscored_run
from offsecgym.experiment import MonolithicExperimentRunner
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
    return ModelTurn(
        response_id="resp_mock",
        status="completed",
        output=output,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
    )


def model_spec(*, tokens: int = 500, calls: int = 4):
    base = scripted_spec()
    return base.model_copy(
        update={
            "orchestrator": "monolithic",
            "memory": "transcript",
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

    async def complete(self, model, instructions, input_items, tools, max_output_tokens):
        self.requests.append((model, instructions, list(input_items), tools, max_output_tokens))
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
async def test_model_usage_and_agent_budget_are_scored(tmp_path, monkeypatch) -> None:
    events = MemoryEvents()
    provider = QueueProvider(turn(input_tokens=9, output_tokens=7))
    outcome = await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), events, provider
    ).run(model_spec(tokens=15))
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
async def test_cancelled_model_call_closes_call_and_run(tmp_path, monkeypatch) -> None:
    class CancelledProvider:
        async def complete(self, *args):
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
            ModelSpec(provider="openai", name="mock-model"),
            "synthetic only",
            [{"role": "user", "content": "hello"}],
            model_tools(),
            128,
        )
    )
    assert result.usage.input_tokens == 12
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
                "budget": {"max_cost_usd": 0.01},
            }
        )
    assert unscored_run("provider_failed").score_valid is False
    assert unscored_run("cancelled").false_negatives is None
