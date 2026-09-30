"""OpenRouter Responses contract and matched Kimi K3 diagnostic specifications."""

from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path

import pytest
import yaml

from offsecgym.providers.base import ProviderFailure
from offsecgym.providers.openrouter import OpenRouterResponsesProvider
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import model_tools

CONFIGS = Path(__file__).parents[2] / "experiments" / "configs"


def test_kimi_diagnostic_specs_match_except_name_and_memory() -> None:
    transcript = ExperimentSpec.model_validate(
        yaml.safe_load((CONFIGS / "kimi-k3-transcript-diagnostic.yaml").read_text())
    )
    structured = ExperimentSpec.model_validate(
        yaml.safe_load((CONFIGS / "kimi-k3-structured-diagnostic.yaml").read_text())
    )
    assert transcript.memory == "transcript"
    assert structured.memory == "structured"
    assert transcript.model == structured.model
    assert transcript.model is not None
    assert transcript.model.provider == "openrouter"
    assert transcript.model.name == "moonshotai/kimi-k3"
    assert transcript.model.reasoning == "high"
    assert transcript.model_dump(exclude={"name", "memory"}) == structured.model_dump(
        exclude={"name", "memory"}
    )


def test_openrouter_responses_request_and_usage_contract(monkeypatch) -> None:
    provider = OpenRouterResponsesProvider("test-secret")
    model = ExperimentSpec.model_validate(
        yaml.safe_load((CONFIGS / "kimi-k3-transcript-diagnostic.yaml").read_text())
    ).model
    assert model is not None
    request = provider.prepare_request(
        model,
        "synthetic range only",
        [{"role": "user", "content": "probe"}],
        model_tools(),
        1024,
    )
    assert request["model"] == "moonshotai/kimi-k3"
    assert request["reasoning"] == {"effort": "high"}
    assert request["store"] is False
    assert request["parallel_tool_calls"] is False
    assert "test-secret" not in json.dumps(request)
    captured = {}
    response = {
        "id": "resp_openrouter_1",
        "status": "completed",
        "output": [{"type": "function_call", "name": "http_request", "call_id": "call_1"}],
        "usage": {"input_tokens": 123, "output_tokens": 45},
        "openrouter_metadata": {"provider_name": "test-provider"},
    }

    def fake_open(http_request, *, timeout):
        captured["url"] = http_request.full_url
        captured["metadata"] = http_request.get_header("X-openrouter-metadata")
        captured["timeout"] = timeout
        return BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(provider._opener, "open", fake_open)
    result = asyncio.run(provider.complete(request))
    assert captured["url"] == "https://openrouter.ai/api/v1/responses"
    assert captured["metadata"] == "enabled"
    assert result.usage.input_tokens == 123
    assert result.output[0]["type"] == "function_call"
    assert result.raw_response["openrouter_metadata"]["provider_name"] == "test-provider"


def test_openrouter_quota_failure_is_a_provider_failure(monkeypatch) -> None:
    from urllib.error import HTTPError

    provider = OpenRouterResponsesProvider("test-secret")

    def reject(*args, **kwargs):
        raise HTTPError(provider.URL, 402, "payment required", {}, BytesIO(b"{}"))

    monkeypatch.setattr(provider._opener, "open", reject)
    with pytest.raises(ProviderFailure) as error:
        provider._post({"model": "moonshotai/kimi-k3"})
    assert error.value.reason_code == "provider_quota_exhausted"
