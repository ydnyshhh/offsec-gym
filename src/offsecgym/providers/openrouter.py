"""OpenRouter OpenResponses adapter with the same audited model-turn contract."""

from __future__ import annotations

from offsecgym.providers.openai import OpenAIResponsesProvider
from offsecgym.schemas.specs import ModelSpec


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
