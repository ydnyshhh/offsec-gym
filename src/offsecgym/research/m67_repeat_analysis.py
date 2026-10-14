"""Offline, descriptive model-request and adjacent-response fingerprints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from offsecgym.providers.openai import request_wire_bytes

TRANSPORT_ONLY_TOP_LEVEL = frozenset(
    {"request_id", "trace_id", "event_id", "transport_timestamp", "transport_metadata"}
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def request_fingerprint(
    payload: dict[str, object],
    *,
    prior_response: dict[str, object] | None = None,
    context_token_count: int | None = None,
    endpoint: str | None = None,
    resolved_model_revision: str | None = None,
) -> dict[str, str | int | None]:
    """Strip only named top-level transport keys; retain every model-visible field."""
    semantic = {k: v for k, v in payload.items() if k not in TRANSPORT_ONLY_TOP_LEVEL}
    inputs = semantic.get("input", [])
    messages = inputs if isinstance(inputs, list) else []
    tool_results = [
        item
        for item in messages
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    user_context = [
        item for item in messages if isinstance(item, dict) and item.get("role") == "user"
    ]
    reasoning = semantic.get("reasoning")
    return {
        "raw_request_sha256": hashlib.sha256(request_wire_bytes(payload)).hexdigest(),
        "semantic_request_sha256": _sha(semantic),
        "system_prompt_sha256": _sha(semantic.get("instructions")),
        "user_context_sha256": _sha(user_context),
        "tools_schema_sha256": _sha(semantic.get("tools")),
        "normalized_message_sha256": _sha(messages),
        "normalized_tool_result_sha256": _sha(tool_results),
        "prior_response_sha256": _sha(_policy_output(prior_response)) if prior_response else None,
        "context_token_count": context_token_count,
        "model": str(semantic.get("model")) if semantic.get("model") is not None else None,
        "endpoint": endpoint,
        "resolved_model_revision": resolved_model_revision,
        "reasoning_setting": (
            str(reasoning.get("effort")) if isinstance(reasoning, dict) else None
        ),
    }


def _policy_output(response: dict[str, object] | None) -> list[dict[str, object]]:
    if response is None:
        return []
    output = response.get("output")
    if not isinstance(output, list):
        return []
    return [
        {k: v for k, v in item.items() if k not in {"id", "call_id"}}
        for item in output
        if isinstance(item, dict)
    ]


def _reasoning(response: dict[str, object]) -> list[dict[str, object]]:
    return [item for item in _policy_output(response) if item.get("type") == "reasoning"]


def _tool_batch(response: dict[str, object]) -> list[tuple[str, str]]:
    """M6.6.1-compatible ordered function name and exact argument-string batch."""
    return [
        (str(item.get("name")), str(item.get("arguments")))
        for item in _policy_output(response)
        if item.get("type") == "function_call"
    ]


@dataclass(frozen=True)
class RepeatClassification:
    exact_response_repeat: bool
    exact_reasoning_repeat: bool
    exact_tool_batch_repeat: bool
    same_semantic_request_same_response: bool
    different_semantic_request_same_response: bool
    same_context_different_request_transport: bool
    repeated_state_changing_tool_batch: bool
    repeated_read_only_tool_batch: bool
    repeated_call_input_tokens: int | None
    repeated_call_output_tokens: int | None
    repeated_call_estimated_cost_usd: float | None


def classify_adjacent(
    previous_request: dict[str, object],
    previous_response: dict[str, object],
    request: dict[str, object],
    response: dict[str, object],
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost_usd: float | None = None,
) -> RepeatClassification:
    before = request_fingerprint(previous_request)
    after = request_fingerprint(request, prior_response=previous_response)
    exact_response = bool(_policy_output(response)) and (
        _policy_output(previous_response) == _policy_output(response)
    )
    reasoning = bool(_reasoning(response)) and _reasoning(previous_response) == _reasoning(response)
    batch = _tool_batch(response)
    tool_repeat = bool(batch) and _tool_batch(previous_response) == batch
    methods: list[str] = []
    for name, arguments in batch:
        if name != "http_request":
            continue
        try:
            parsed = json.loads(arguments)
        except ValueError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("method"), str):
            methods.append(parsed["method"])
    same_semantic = before["semantic_request_sha256"] == after["semantic_request_sha256"]
    return RepeatClassification(
        exact_response_repeat=exact_response,
        exact_reasoning_repeat=reasoning,
        exact_tool_batch_repeat=tool_repeat,
        same_semantic_request_same_response=same_semantic and exact_response,
        different_semantic_request_same_response=not same_semantic and exact_response,
        same_context_different_request_transport=(
            same_semantic and before["raw_request_sha256"] != after["raw_request_sha256"]
        ),
        repeated_state_changing_tool_batch=tool_repeat
        and any(method in {"POST", "PUT", "PATCH", "DELETE"} for method in methods),
        repeated_read_only_tool_batch=tool_repeat
        and bool(methods)
        and all(method == "GET" for method in methods),
        repeated_call_input_tokens=input_tokens if exact_response else None,
        repeated_call_output_tokens=output_tokens if exact_response else None,
        repeated_call_estimated_cost_usd=estimated_cost_usd if exact_response else None,
    )
