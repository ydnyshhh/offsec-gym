"""Conservative, source-bound taxonomy of adjacent model-visible context deltas."""

from __future__ import annotations

import json
from collections import Counter
from typing import Literal

from offsecgym.research.m67_repeat_analysis import classify_adjacent, request_fingerprint
from offsecgym.research.m673_audit import AuditSnapshot

Category = Literal[
    "no material semantic change",
    "new unrelated observation",
    "new task-relevant observation",
    "authorization/state changed",
    "deadline changed",
    "unknown",
]


def _outputs(request: dict[str, object]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in request.get("input", []):
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
        raw = item.get("output")
        if not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            result.append({"call_id": item.get("call_id"), **parsed})
    return result


def _http_calls(response: dict[str, object]) -> list[dict[str, object]]:
    calls = []
    for item in response.get("output", []):
        if (
            not isinstance(item, dict)
            or item.get("type") != "function_call"
            or item.get("name") != "http_request"
        ):
            continue
        try:
            value = json.loads(item.get("arguments", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            calls.append(value)
    return calls


def _http_observation_content(request: dict[str, object]) -> list[tuple[object, ...]]:
    """M6.7.2's HTTP-content comparison, kept separate from relevance."""
    return [
        (
            item.get("http_status"),
            item.get("body_text"),
            item.get("truncated"),
            item.get("status"),
            item.get("reason_code"),
        )
        for item in _outputs(request)
        if "http_status" in item and "body_text" in item
    ]


def classify_context_delta(
    prior_request: dict[str, object],
    prior_response: dict[str, object],
    current_request: dict[str, object],
) -> tuple[Category, dict[str, object]]:
    """Decision relevance requires a source-matched object/route or explicit task state.

    A changed body alone is insufficient. Unparsed and ambiguous deltas stay unknown.
    The classifier cannot infer provider internals or intent.
    """
    before = request_fingerprint(prior_request)
    after = request_fingerprint(current_request)
    if before["semantic_request_sha256"] == after["semantic_request_sha256"]:
        return "no material semantic change", {"semantic_context_changed": False}
    old = _outputs(prior_request)
    old_ids = {str(item.get("call_id")) for item in old}
    new = [item for item in _outputs(current_request) if str(item.get("call_id")) not in old_ids]
    if not new:
        return "unknown", {"semantic_context_changed": True, "new_tool_results": 0}
    http = [item for item in new if "http_status" in item]
    if not http:
        return "unknown", {"semantic_context_changed": True, "new_tool_results": len(new)}
    current_calls = _http_calls(prior_response)
    old_http = [item for item in old if "http_status" in item]
    changed_http = bool(old_http) and any(
        (item.get("http_status"), item.get("body_text"))
        != (old_http[-1].get("http_status"), old_http[-1].get("body_text"))
        for item in http
    )
    # A newly returned field is not necessarily a *changed state*. Only an
    # exact same-object before/after comparison justifies that claim.
    old_objects = []
    new_objects = []
    for source, destination in ((old_http, old_objects), (http, new_objects)):
        for item in source:
            try:
                body = json.loads(str(item.get("body_text", "")))
            except ValueError:
                continue
            if isinstance(body, dict) and isinstance(body.get("id"), str):
                destination.append(body)
    by_id = {str(obj["id"]): obj for obj in old_objects}
    paired = [(by_id[str(obj["id"])], obj) for obj in new_objects if str(obj["id"]) in by_id]
    deadline = any(
        a.get("due_tick") != b.get("due_tick")
        for a, b in paired
        if "due_tick" in a and "due_tick" in b
    )
    authorization = any(
        a.get(field) != b.get(field)
        for a, b in paired
        for field in ("roles", "permissions", "authorized")
        if field in a and field in b
    )
    state_changed = any(
        a.get("status") != b.get("status") for a, b in paired if "status" in a and "status" in b
    )
    if deadline:
        category: Category = "deadline changed"
    elif authorization:
        category = "authorization/state changed"
    elif state_changed and any(
        str(obj["id"]) in str(call.get("path", "")) for _, obj in paired for call in current_calls
    ):
        category = "new task-relevant observation"
    else:
        category = "unknown"
    return category, {
        "semantic_context_changed": True,
        "new_tool_results": len(new),
        "new_http_results": len(http),
        "changed_http_body_or_status": changed_http,
        "decision_relevant_context_changed": category
        in {"new task-relevant observation", "authorization/state changed", "deadline changed"},
    }


def analyze_context_delta(
    snapshot: AuditSnapshot,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    counts: dict[str, Counter[str]] = {}
    private_rows: list[dict[str, object]] = []
    all_pairs = 0
    both_http = 0
    changed_http_content = 0
    repeated_usage = Counter()
    for cell in snapshot.cells:
        if cell.row["trace_auditable"] is not True:
            continue
        for prior, current in zip(cell.calls, cell.calls[1:], strict=False):
            all_pairs += 1
            repeat = classify_adjacent(
                prior.request,
                prior.response,
                current.request,
                current.response,
                input_tokens=current.completed.input_tokens
                if cell.row["resource_auditable"]
                else None,
                output_tokens=current.completed.output_tokens
                if cell.row["resource_auditable"]
                else None,
                estimated_cost_usd=(
                    current.completed.estimated_cost_usd if cell.row["resource_auditable"] else None
                ),
            )
            category, flags = classify_context_delta(prior.request, prior.response, current.request)
            bucket = counts.setdefault(category, Counter())
            bucket["adjacent_pairs"] += 1
            bucket["exact_repeats"] += repeat.exact_response_repeat
            bucket["repeated_state_changing_batches"] += repeat.repeated_state_changing_tool_batch
            if repeat.exact_response_repeat:
                repeated_usage["resource_auditable_calls"] += cell.row["resource_auditable"] is True
                if repeat.repeated_call_input_tokens is not None:
                    repeated_usage["input_tokens"] += repeat.repeated_call_input_tokens
                if repeat.repeated_call_output_tokens is not None:
                    repeated_usage["output_tokens"] += repeat.repeated_call_output_tokens
                if repeat.repeated_call_estimated_cost_usd is not None:
                    repeated_usage["estimated_cost_usd"] += repeat.repeated_call_estimated_cost_usd
                old_http_content = _http_observation_content(prior.request)
                new_http_content = _http_observation_content(current.request)
                both_http += bool(old_http_content and new_http_content)
                changed_http_content += bool(
                    old_http_content and new_http_content and old_http_content != new_http_content
                )
                private_rows.append(
                    {
                        "order": cell.assignment["order"],
                        "run_id": cell.record["run_id"],
                        "prior_call_id": str(prior.start.call_id),
                        "call_id": str(current.start.call_id),
                        "prior_request_sha256": prior.start.request_sha256,
                        "request_sha256": current.start.request_sha256,
                        "category": category,
                        "exact_output_repeat": True,
                        "exact_tool_batch_repeat": repeat.exact_tool_batch_repeat,
                        "repeated_state_changing_batch": repeat.repeated_state_changing_tool_batch,
                        **flags,
                    }
                )
    if len(private_rows) != 96 or all_pairs < 96 or (both_http, changed_http_content) != (85, 61):
        raise ValueError("adjacent repeat count differs from frozen audit")
    table = {
        category: dict(counts.get(category, Counter()))
        for category in (
            "no material semantic change",
            "new unrelated observation",
            "new task-relevant observation",
            "authorization/state changed",
            "deadline changed",
            "unknown",
        )
    }
    return {
        "adjacent_pairs": all_pairs,
        "exact_repeated_outputs": len(private_rows),
        "repeats_both_requests_with_http_observations": both_http,
        "repeats_with_changed_http_content_when_both_present": changed_http_content,
        "repeated_call_usage_when_resource_auditable": dict(repeated_usage),
        "table": table,
        "decision_relevance_rule": (
            "Exact same-object state change or authorization/deadline change; "
            "ambiguous deltas remain unknown"
        ),
        "provider_caching_inference": None,
    }, private_rows
