"""Reproducible preflight estimate for providers without a token-count API."""

import json
from math import ceil
from typing import Any

from offsecgym.schemas.specs import ModelSpec


def estimate_input_tokens(payload: dict[str, Any], model: ModelSpec) -> tuple[int, int]:
    # Match the OpenAI/OpenRouter adapter's outbound JSON serialization.
    request_bytes = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    reserved_input_tokens = (
        ceil(request_bytes / model.input_reservation_bytes_per_token)
        + model.input_reservation_margin_tokens
    )
    return reserved_input_tokens, request_bytes
