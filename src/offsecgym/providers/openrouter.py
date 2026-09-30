"""OpenRouter OpenResponses adapter with the same audited model-turn contract."""

from __future__ import annotations

from offsecgym.providers.openai import OpenAIResponsesProvider


class OpenRouterResponsesProvider(OpenAIResponsesProvider):
    URL = "https://openrouter.ai/api/v1/responses"
    PROVIDER = "openrouter"
    EXTRA_HEADERS = {"X-OpenRouter-Metadata": "enabled"}
