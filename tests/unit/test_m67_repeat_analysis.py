"""Transport/semantic distinction and descriptive adjacent-repeat detection."""

from __future__ import annotations

import copy
import json

from offsecgym.research.m67_repeat_analysis import classify_adjacent, request_fingerprint


def _request(method="POST"):
    return {
        "model": "fake",
        "instructions": "Explore only the synthetic range",
        "input": [
            {"role": "user", "content": "test the observed state"},
            {
                "type": "function_call_output",
                "call_id": "visible-call",
                "output": json.dumps({"method": method}),
            },
        ],
        "tools": [{"name": "http_request", "parameters": {"type": "object"}}],
        "reasoning": {"effort": "high"},
    }


def _response(method="POST"):
    return {
        "id": "transport-response",
        "output": [
            {"type": "reasoning", "id": "generated-item", "summary": "inspect result"},
            {
                "type": "function_call",
                "id": "generated-call",
                "call_id": "generated-id",
                "name": "http_request",
                "arguments": json.dumps({"method": method, "path": "/api/changes"}),
            },
        ],
    }


def test_transport_fields_do_not_change_semantic_fingerprint():
    first = _request()
    second = copy.deepcopy(first)
    second["request_id"] = "new-transport-id"
    second["transport_timestamp"] = "tomorrow"
    a, b = request_fingerprint(first), request_fingerprint(second)
    assert a["raw_request_sha256"] != b["raw_request_sha256"]
    assert a["semantic_request_sha256"] == b["semantic_request_sha256"]
    assert a["normalized_message_sha256"] == b["normalized_message_sha256"]
    second["input"][0]["content"] = "changed visible state"
    assert request_fingerprint(second)["semantic_request_sha256"] != a["semantic_request_sha256"]


def test_adjacent_repeat_is_classified_without_suppression():
    first = _request()
    second = copy.deepcopy(first)
    second["request_id"] = "new-transport-id"
    repeated = classify_adjacent(
        first,
        _response(),
        second,
        _response(),
        input_tokens=90,
        output_tokens=30,
        estimated_cost_usd=0.004,
    )
    assert repeated.exact_response_repeat
    assert repeated.exact_reasoning_repeat
    assert repeated.exact_tool_batch_repeat
    assert repeated.same_semantic_request_same_response
    assert repeated.same_context_different_request_transport
    assert repeated.repeated_state_changing_tool_batch
    assert not repeated.repeated_read_only_tool_batch
    assert repeated.repeated_call_input_tokens == 90
    assert repeated.repeated_call_estimated_cost_usd == 0.004
    changed = copy.deepcopy(second)
    changed["input"][0]["content"] = "new visible state"
    different = classify_adjacent(first, _response(), changed, _response())
    assert different.different_semantic_request_same_response
    read_only = classify_adjacent(first, _response("GET"), second, _response("GET"))
    assert read_only.repeated_read_only_tool_batch
    assert not read_only.repeated_state_changing_tool_batch
