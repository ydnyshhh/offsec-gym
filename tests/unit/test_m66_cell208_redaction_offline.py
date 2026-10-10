"""The cell-208 audit exception requires three independent body bindings."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))

import m66_cell208_redaction_offline as offline  # noqa: E402


def _fixture():
    body = {"project_id": "example", "budget_tokens": 1000}
    action = {
        "originating_tool_call_id": "call-1",
        "method": "POST",
        "identity_id": "identity-1",
        "path_sha256": offline._sha256(b"/projects/example/changes"),
        "body_sha256": offline._sha256(offline._canonical(body)),
    }
    request = SimpleNamespace(
        action_id=offline.ACTION_ID,
        run_id=offline.RUN_ID,
        json_body={"project_id": "example", "budget_tokens": "*"},
    )
    response = {
        "output": [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "http_request",
                "arguments": json.dumps(
                    {
                        "method": "POST",
                        "path": "/projects/example/changes",
                        "identity_id": "identity-1",
                        "body_json": json.dumps(body),
                    }
                ),
            }
        ]
    }

    def redact(value):
        return {**value, "budget_tokens": "*"}

    return body, action, request, response, redact


def test_exact_model_body_reconstructs_only_redacted_field() -> None:
    body, action, request, response, redact = _fixture()
    assert offline._bound_original_body(action, request, response, redact) == body


@pytest.mark.parametrize("change", ["body_hash", "tool_id", "artifact", "extra_redaction"])
def test_any_broken_binding_fails_closed(change: str) -> None:
    _, action, request, response, redact = _fixture()
    if change == "body_hash":
        action["body_sha256"] = "0" * 64
    elif change == "tool_id":
        action["originating_tool_call_id"] = "another-call"
    elif change == "artifact":
        request.json_body["project_id"] = "different"
    elif change == "extra_redaction":
        request.json_body["project_id"] = "*"
    with pytest.raises(ValueError, match="bound|do not bind"):
        offline._bound_original_body(action, request, response, redact)
