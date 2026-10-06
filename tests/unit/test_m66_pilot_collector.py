"""Operational guards for the approved, excluded M6.6 pilot."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from offsecgym.providers.base import ModelTurn, ProviderFailure
from offsecgym.providers.openrouter import OpenRouterResponsesProvider

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m66_collect_pilot as collector  # noqa: E402


@pytest.mark.asyncio
async def test_endpoint_drift_stops_following_paid_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = 0

    async def unexpected_endpoint(
        _provider: OpenRouterResponsesProvider, _payload: dict[str, object]
    ) -> ModelTurn:
        nonlocal requests
        requests += 1
        return ModelTurn.model_validate(
            {
                "status": "completed",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "raw_response": {
                    "openrouter_metadata": {
                        "endpoints": {
                            "available": [
                                {
                                    "selected": True,
                                    "model": "another-revision",
                                    "provider": "another-provider",
                                }
                            ]
                        }
                    }
                },
            }
        )

    monkeypatch.setattr(OpenRouterResponsesProvider, "complete", unexpected_endpoint)
    guard = collector.EndpointGuard(("moonshotai/kimi-k3-20260715", "Moonshot AI"))
    provider = guard.provider("test-key")
    with pytest.raises(ProviderFailure, match="pilot_endpoint_drift"):
        await provider.complete({})
    with pytest.raises(ProviderFailure, match="pilot_provider_stopped"):
        await provider.complete({})
    assert requests == 1


def test_price_drift_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, *_args):
            return json.dumps(
                {
                    "data": {
                        "endpoints": [
                            {
                                "name": "Moonshot AI | moonshotai/kimi-k3-20260715",
                                "model_id": "moonshotai/kimi-k3",
                                "pricing": {"prompt": "0.000004", "completion": "0.000015"},
                            }
                        ]
                    }
                }
            ).encode()

    monkeypatch.setattr(collector, "urlopen", lambda *_a, **_k: Response())
    manifest = {
        "expected_selected_endpoint": {
            "revision": "moonshotai/kimi-k3-20260715",
            "upstream_provider": "Moonshot AI",
        },
        "model_request": {"name": "moonshotai/kimi-k3"},
    }
    with pytest.raises(ValueError, match="endpoint or price has drifted"):
        collector._fresh_endpoint_check(manifest)
