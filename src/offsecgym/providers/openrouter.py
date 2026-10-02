"""OpenRouter OpenResponses adapter with the same audited model-turn contract."""

from __future__ import annotations

from offsecgym.providers.openai import OpenAIResponsesProvider
from offsecgym.schemas.specs import ModelSpec


def selected_endpoint(raw_response: dict[str, object]) -> tuple[str, str] | None:
    """Read the actual selected model/provider from OpenRouter response metadata."""
    metadata = raw_response.get("openrouter_metadata")
    if not isinstance(metadata, dict):
        return None
    endpoints = metadata.get("endpoints")
    if not isinstance(endpoints, dict):
        return None
    available = endpoints.get("available")
    if not isinstance(available, list):
        return None
    selected = [
        item for item in available if isinstance(item, dict) and item.get("selected") is True
    ]
    if len(selected) != 1:
        return None
    model = selected[0].get("model")
    provider = selected[0].get("provider")
    if not isinstance(model, str) or not model or not isinstance(provider, str) or not provider:
        return None
    return model, provider


class OpenRouterResponsesProvider(OpenAIResponsesProvider):
    URL = "https://openrouter.ai/api/v1/responses"
    PROVIDER = "openrouter"
    EXTRA_HEADERS = {"X-OpenRouter-Metadata": "enabled"}

    def prepare_request(
        self,
        model: ModelSpec,
        instructions: str,
        input_items: list[dict[str, object]],
        tools: list[dict[str, object]],
        max_output_tokens: int,
    ) -> dict[str, object]:
        payload = super().prepare_request(
            model, instructions, input_items, tools, max_output_tokens
        )
        if model.upstream_provider is not None:
            payload["provider"] = {
                "order": [model.upstream_provider],
                "allow_fallbacks": False,
            }
        return payload
