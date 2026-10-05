"""The first range's explicit action scope policy."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import ExperimentContext


def path_allowed(path: str) -> bool:
    try:
        parsed = urlsplit(path)
    except ValueError:
        return False
    return (
        path.startswith("/")
        and not path.startswith("//")
        and not parsed.scheme
        and not parsed.netloc
        and not parsed.fragment
        and not any(character in path for character in ("\r", "\n", "\x00", "\\"))
    )


def scope_reason(
    action: ActionRequest, context: ExperimentContext, family: str = "hello"
) -> str | None:
    if action.run_id != context.run_id:
        return "run_mismatch"
    if action.kind != "http_request" or action.destination != family:
        return "destination_out_of_scope"
    if not path_allowed(action.path):
        return "invalid_path"
    if family == "hello":
        if action.method != "GET":
            return "method_not_allowed"
        if action.identity_id is not None or action.json_body is not None:
            return "invalid_request"
    elif family in {"saas", "enterprise_change_control_v1"}:
        if action.method not in {"GET", "POST"}:
            return "method_not_allowed"
        if urlsplit(action.path).path == "/api/login":
            return "login_is_gateway_managed"
        if (
            action.identity_id is not None
            and action.identity_id not in context.allowed_identity_ids
        ):
            return "identity_out_of_scope"
        if action.method == "GET" and action.json_body is not None:
            return "invalid_request"
        if action.json_body is not None and len(json.dumps(action.json_body).encode()) > 4096:
            return "request_body_too_large"
    else:
        return "destination_out_of_scope"
    return None
