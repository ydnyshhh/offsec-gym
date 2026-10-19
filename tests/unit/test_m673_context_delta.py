"""Exact repeats and conservative context relevance stay distinct."""

from __future__ import annotations

import json

from offsecgym.research.m67_repeat_analysis import classify_adjacent
from offsecgym.research.m673_context_delta import classify_context_delta
from offsecgym.research.m673_experiment_design import static_request_perturbation


def _request(status: str, object_id: str = "synthetic-a") -> dict[str, object]:
    return {
        "model": "synthetic",
        "instructions": "Synthetic task",
        "tools": [],
        "input": [
            {
                "type": "function_call_output",
                "call_id": status,
                "output": json.dumps(
                    {
                        "http_status": 200,
                        "body_text": json.dumps(
                            {
                                "id": object_id,
                                "status": status,
                            }
                        ),
                    }
                ),
            }
        ],
    }


def _response(object_id: str = "synthetic-a") -> dict[str, object]:
    return {
        "output": [
            {
                "type": "function_call",
                "name": "http_request",
                "call_id": "synthetic-call",
                "arguments": json.dumps(
                    {
                        "method": "GET",
                        "path": f"/api/changes/{object_id}",
                    }
                ),
            }
        ]
    }


def test_repeated_output_with_same_object_status_change_is_relevant() -> None:
    before, after, response = _request("PENDING"), _request("APPROVED"), _response()
    assert classify_adjacent(before, response, after, response).exact_response_repeat
    category, flags = classify_context_delta(before, response, after)
    assert category == "new task-relevant observation"
    assert flags["decision_relevant_context_changed"]


def test_unrelated_object_status_change_remains_unknown() -> None:
    category, flags = classify_context_delta(
        _request("PENDING", "synthetic-a"),
        _response("synthetic-a"),
        _request("APPROVED", "synthetic-b"),
    )
    assert category == "unknown"
    assert not flags["decision_relevant_context_changed"]


def test_transport_metadata_only_is_no_material_semantic_change() -> None:
    before = _request("PENDING")
    after = static_request_perturbation(before, field="transport_metadata", value={"attempt": 2})
    category, flags = classify_context_delta(before, _response(), after)
    assert category == "no material semantic change"
    assert not flags["semantic_context_changed"]
    assert "transport_metadata" not in before
