"""The first range's explicit action scope policy."""

from __future__ import annotations

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


def scope_reason(action: ActionRequest, context: ExperimentContext) -> str | None:
    if action.run_id != context.run_id:
        return "run_mismatch"
    if action.kind != "http_request" or action.destination != "hello":
        return "destination_out_of_scope"
    if action.method != "GET":
        return "method_not_allowed"
    if not path_allowed(action.path):
        return "invalid_path"
    return None
