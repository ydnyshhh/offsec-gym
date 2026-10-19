"""Offline replay of model-authored experiment proposals; no tool dispatch."""

from __future__ import annotations

import json
import re
from collections import Counter
from uuid import UUID

from pydantic import Field, ValidationError

from offsecgym.research.m673_audit import AuditSnapshot, CellEvidence
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import ModelToolRejected
from offsecgym.solver.experiment_state import StartExperimentArgs

UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class OpaqueStartArgs(StartExperimentArgs):
    target_objects: tuple[str, ...] = Field(max_length=8)


class PublicReference(StrictModel):
    kind: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=240)


class StructuredStartArgs(StartExperimentArgs):
    target_objects: tuple[PublicReference, ...] = Field(max_length=8)


class OptionalStartArgs(StartExperimentArgs):
    target_objects: tuple[UUID, ...] | None = None


def parses(schema: type[StrictModel], value: object) -> bool:
    try:
        schema.model_validate(value)
    except ValidationError:
        return False
    return True


def reference_category(value: object) -> str:
    if value is None:
        return "null"
    if value == "":
        return "empty_string"
    if not isinstance(value, str):
        return "other_or_ambiguous"
    try:
        UUID(value)
        return "valid_uuid"
    except ValueError:
        pass
    if UUID_PATTERN.search(value):
        return "route_or_url" if "/" in value or "://" in value else "valid_uuid_in_other_structure"
    if value.startswith(("/", "http://", "https://")):
        return "route_or_url"
    if any(character.isspace() for character in value):
        return "natural_language_description"
    if value.casefold() in {"change", "job", "project", "environment", "user", "role"}:
        return "entity_type_label"
    return "opaque_or_entity_name"


def _delivered_context(requests: list[dict[str, object]]) -> str:
    """Find delivered text, including tool echoes; this does not establish provenance."""
    texts: list[str] = []
    for request in requests:
        instructions = request.get("instructions")
        if isinstance(instructions, str):
            texts.append(instructions)
        items = request.get("input")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("role") in {"user", "system", "developer"}:
                texts.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
            if item.get("type") == "function_call_output":
                raw = item.get("output")
                if isinstance(raw, str):
                    if "invalid_tool_arguments" in raw and "invalid_tool_call" in raw:
                        continue
                    texts.append(raw)
    return "\n".join(texts).casefold()


def ground_reference(value: object, requests: list[dict[str, object]]) -> dict[str, object]:
    visible_text = _delivered_context(requests)
    if not isinstance(value, str) or not value:
        return {
            "identifier_observed": False,
            "entity_name_observed": False,
            "context_observed": False,
            "public_source_grounded": None,
            "object_resolved": None,
        }
    try:
        uuid = str(UUID(value))
    except ValueError:
        embedded = UUID_PATTERN.findall(value)
        uuid = str(UUID(embedded[0])) if len(embedded) == 1 else None
    observed_uuid = bool(uuid and uuid.casefold() in visible_text)
    observed_name = bool(uuid is None and value.casefold() in visible_text)
    return {
        "identifier_observed": observed_uuid,
        "entity_name_observed": observed_name,
        "context_observed": observed_uuid or observed_name,
        # Delivered instructions and function outputs may echo model-authored data.
        # Proven public origin and object/type resolution require a separate,
        # event-linked gateway provenance audit; neither is inferred here.
        "public_source_grounded": None,
        "object_resolved": None,
    }


def _proposals(cell: CellEvidence) -> list[dict[str, object]]:
    results = []
    for call in cell.calls:
        output = call.response.get("output")
        if not isinstance(output, list):
            continue
        for index, item in enumerate(output):
            if (
                isinstance(item, dict)
                and item.get("type") == "function_call"
                and item.get("name") == "start_experiment"
            ):
                results.append(
                    {
                        "call": call,
                        "sequence": call.completed.sequence_number,
                        "index": index,
                        "tool_call_id": item.get("call_id"),
                        "raw_arguments": item.get("arguments"),
                    }
                )
    return results


def analyze_interface(snapshot: AuditSnapshot) -> tuple[dict[str, object], list[dict[str, object]]]:
    private_rows: list[dict[str, object]] = []
    categories: Counter[str] = Counter()
    wrappers: Counter[str] = Counter()
    schema_replay: Counter[str] = Counter()
    grounding: Counter[str] = Counter()
    feedback: Counter[str] = Counter()
    seen: set[tuple[str, str, str]] = set()
    for cell in snapshot.cells:
        if cell.row["trace_auditable"] is not True:
            continue
        proposals = _proposals(cell)
        if not proposals:
            continue
        rejection = {
            (event.model_call_id, event.tool_call_id): event
            for event in cell.trace
            if isinstance(event, ModelToolRejected) and event.tool_name == "start_experiment"
        }
        if len(rejection) != sum(
            isinstance(event, ModelToolRejected) and event.tool_name == "start_experiment"
            for event in cell.trace
        ):
            raise ValueError("duplicate experiment rejection identity")
        requests_seen: list[dict[str, object]] = []
        for call in cell.calls:
            requests_seen.append(call.request)
            local = [p for p in proposals if p["call"] is call]
            for proposal in local:
                raw = proposal["raw_arguments"]
                try:
                    args = json.loads(raw)
                except (TypeError, ValueError):
                    args = None
                key = (
                    str(cell.record["run_id"]),
                    str(call.start.call_id),
                    str(proposal["tool_call_id"]),
                )
                if key in seen:
                    raise ValueError("duplicate model proposal provenance")
                seen.add(key)
                error = rejection.get((call.start.call_id, str(proposal["tool_call_id"])[:128]))
                entries = args.get("target_objects") if isinstance(args, dict) else None
                entries = entries if isinstance(entries, list) else []
                refs = []
                for entry in entries:
                    category = reference_category(entry)
                    ground = ground_reference(entry, requests_seen)
                    refs.append({"original_value": entry, "category": category, **ground})
                    categories[category] += 1
                    if category == "valid_uuid_in_other_structure":
                        wrappers[
                            "typed_prefix"
                            if isinstance(entry, str) and re.match(r"^[A-Za-z_]+:", entry)
                            else "other_wrapper"
                        ] += 1
                    grounding["context_entries"] += bool(ground["context_observed"])
                    grounding["total_entries"] += 1
                parse = {
                    "strict_uuid": parses(StartExperimentArgs, args),
                    "optional_uuid": parses(OptionalStartArgs, args),
                    "opaque_string": parses(OpaqueStartArgs, args),
                    "structured_reference": parses(StructuredStartArgs, args),
                }
                strict_errors: list[dict[str, str]] = []
                try:
                    StartExperimentArgs.model_validate(args)
                except ValidationError as exc:
                    strict_errors = [
                        {"field": ".".join(map(str, item["loc"])), "type": item["type"]}
                        for item in exc.errors(include_url=False, include_input=False)
                    ]
                if not strict_errors or any(
                    not item["field"].startswith("target_objects") for item in strict_errors
                ):
                    raise ValueError("experiment schema failure differs from pinned UUID boundary")
                invalid_call_index = (
                    sum(
                        isinstance(event, ModelToolRejected)
                        and event.task_id == error.task_id
                        and event.sequence_number <= error.sequence_number
                        for event in cell.trace
                    )
                    if error is not None
                    else None
                )
                schema_replay.update(name for name, accepted in parse.items() if accepted)
                later_calls = [
                    p
                    for p in proposals
                    if (p["sequence"], p["index"]) > (proposal["sequence"], proposal["index"])
                ]
                retry = later_calls[0] if later_calls else None
                next_request = next(
                    (
                        c.request
                        for c in cell.calls
                        if c.start.sequence_number > call.start.sequence_number
                    ),
                    None,
                )
                visible_feedback = False
                if next_request is not None:
                    visible_feedback = any(
                        isinstance(item, dict)
                        and item.get("type") == "function_call_output"
                        and item.get("call_id") == proposal["tool_call_id"]
                        and "invalid_tool_arguments" in str(item.get("output", ""))
                        for item in next_request.get("input", [])
                    )
                feedback["rejected"] += error is not None
                feedback["feedback_visible_next_request"] += visible_feedback
                feedback["later_experiment_proposal"] += retry is not None
                retry_changed = None
                if retry is not None:
                    try:
                        next_args = json.loads(retry["raw_arguments"])
                        retry_changed = next_args.get("target_objects") != entries
                    except (ValueError, TypeError, AttributeError):
                        retry_changed = None
                    feedback["retry_changed_target_objects"] += retry_changed is True
                private_rows.append(
                    {
                        "run_id": key[0],
                        "model_call_id": key[1],
                        "tool_call_id": key[2],
                        "proposal_sequence": proposal["sequence"],
                        "proposal_index": proposal["index"],
                        "order": cell.assignment["order"],
                        "arm": cell.assignment["arm"],
                        "root": cell.assignment["root_id"],
                        "checkpoint": cell.assignment["checkpoint_id"],
                        "request_sha256": call.start.request_sha256,
                        "response_sha256": call.completed.response_sha256,
                        "arguments": args,
                        "references": refs,
                        "schema_parse": parse,
                        "json_parse_succeeded": isinstance(args, dict),
                        "strict_schema_errors": strict_errors,
                        "invalid_call_budget_consumed": error is not None,
                        "invalid_call_index_within_task": invalid_call_index,
                        "invalid_call_limit_reached": invalid_call_index is not None
                        and invalid_call_index >= 3,
                        "controller_rejection": error.reason_code if error else None,
                        "controller_rejection_event_id": str(error.event_id) if error else None,
                        "feedback_visible_next_request": visible_feedback,
                        "later_retry": retry is not None,
                        "retry_changed_target_objects": retry_changed,
                        "would_resolve_to_real_entity": None,
                        "would_support_correct_experiment": None,
                    }
                )
    if len(private_rows) != 18 or len(seen) != 18:
        raise ValueError("experiment proposal count differs from pinned audit")
    if any(row["controller_rejection"] != "invalid_tool_arguments" for row in private_rows):
        raise ValueError("experiment rejection lifecycle differs")
    aggregate = {
        "proposals": len(private_rows),
        "cells": len({row["order"] for row in private_rows}),
        "reference_entries": grounding["total_entries"],
        "reference_categories": dict(sorted(categories.items())),
        "embedded_uuid_wrapper_categories": dict(sorted(wrappers.items())),
        "schema_parse_proposals": dict(sorted(schema_replay.items())),
        "json_parse_succeeded_proposals": sum(row["json_parse_succeeded"] for row in private_rows),
        "strict_schema_failure_fields": dict(
            sorted(
                Counter(
                    error["field"].split(".")[0]
                    for row in private_rows
                    for error in row["strict_schema_errors"]
                ).items()
            )
        ),
        "context_observed_reference_entries": grounding["context_entries"],
        "public_source_grounded_reference_entries": None,
        "object_resolved_reference_entries": None,
        "all_references_context_observed_proposals": sum(
            bool(row["references"]) and all(ref["context_observed"] for ref in row["references"])
            for row in private_rows
        ),
        "correct_experiment_identity_established": None,
        "feedback": dict(sorted(feedback.items())),
        "invalid_call_limit_reached_after_proposal": sum(
            row["invalid_call_limit_reached"] for row in private_rows
        ),
        "schema_acceptance_is_not_grounding": True,
    }
    return aggregate, private_rows
