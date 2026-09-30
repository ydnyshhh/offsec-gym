"""Stateless OpenAI Responses adapter; credentials never enter specs or traces."""

from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import ValidationError

from offsecgym.providers.base import (
    ModelTurn,
    ProviderFailure,
    ProviderRequestError,
)
from offsecgym.schemas.specs import ModelSpec


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class OpenAIResponsesProvider:
    URL = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str, *, timeout_seconds: float = 90) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(_NoRedirect)

    async def complete(
        self,
        model: ModelSpec,
        instructions: str,
        input_items: list[dict[str, object]],
        tools: list[dict[str, object]],
        max_output_tokens: int,
    ) -> ModelTurn:
        if model.provider != "openai":
            raise ProviderRequestError("unsupported_provider")
        payload: dict[str, object] = {
            "model": model.name,
            "instructions": instructions,
            "input": input_items,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "max_output_tokens": max_output_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if model.reasoning is not None:
            payload["reasoning"] = {"effort": model.reasoning}
        response = await asyncio.to_thread(self._post, payload)
        if response.get("status") == "failed":
            raise ProviderFailure("provider_response_failed")
        try:
            usage = response["usage"]
            output = response["output"]
            if not isinstance(output, list) or not all(isinstance(item, dict) for item in output):
                raise TypeError("output is not an item list")
            incomplete = response.get("incomplete_details") or {}
            return ModelTurn.model_validate(
                {
                    "response_id": response.get("id"),
                    "status": response["status"],
                    "output": output,
                    "usage": {
                        "input_tokens": usage["input_tokens"],
                        "output_tokens": usage["output_tokens"],
                    },
                    "incomplete_reason": incomplete.get("reason"),
                }
            )
        except (KeyError, TypeError, AttributeError, ValidationError) as exc:
            raise ProviderRequestError("invalid_provider_response") from exc

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            self.URL,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(2_000_001)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise ProviderFailure("provider_auth_failed", exc.code) from exc
            if exc.code in {408, 429}:
                reason = "provider_rate_limited" if exc.code == 429 else "provider_timeout"
                raise ProviderFailure(reason, exc.code) from exc
            if exc.code >= 500:
                raise ProviderFailure("provider_server_error", exc.code) from exc
            raise ProviderRequestError("provider_request_rejected", exc.code) from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise ProviderFailure("provider_unavailable") from exc
        if len(raw) > 2_000_000:
            raise ProviderRequestError("provider_response_too_large")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderRequestError("invalid_provider_response") from exc
        if not isinstance(result, dict):
            raise ProviderRequestError("invalid_provider_response")
        return result
