"""Provider-neutral boundary for one model turn with explicit usage."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.specs import ModelSpec


class ModelUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelTurn(StrictModel):
    response_id: str | None = None
    status: str
    output: tuple[dict[str, object], ...]
    usage: ModelUsage
    incomplete_reason: str | None = None
    raw_response: dict[str, object]


class ProviderFailure(RuntimeError):
    """A provider or network failure makes the run unscorable."""

    def __init__(
        self,
        reason_code: str,
        http_status: int | None = None,
        raw_response: dict[str, object] | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.http_status = http_status
        self.raw_response = raw_response


class ProviderRequestError(RuntimeError):
    """Our request or provider response contract is invalid; this is harness failure."""

    def __init__(
        self,
        reason_code: str,
        http_status: int | None = None,
        raw_response: dict[str, object] | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.http_status = http_status
        self.raw_response = raw_response


class ModelProvider(Protocol):
    def prepare_request(
        self,
        model: ModelSpec,
        instructions: str,
        input_items: list[dict[str, object]],
        tools: list[dict[str, object]],
        max_output_tokens: int,
    ) -> dict[str, object]: ...

    async def complete(self, request_payload: dict[str, object]) -> ModelTurn: ...
