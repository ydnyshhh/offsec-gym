"""Read-only, identifier-free public taxonomy of retained experiment proposals."""

from __future__ import annotations

import re
from collections import Counter

from pydantic import ValidationError

from offsecgym.research.m681_reference_syntax import (
    ReferenceSyntaxError,
    parse_reference,
    replay_start_arguments,
)
from offsecgym.solver.experiment_state import StartExperimentArgs

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_FRAGMENT = re.compile(_UUID, re.IGNORECASE)
_TYPED = re.compile(rf"[a-z][a-z_]*:{_UUID}\Z", re.ASCII)

REFERENCE_CATEGORIES = (
    "canonical_uuid",
    "supported_typed_uuid",
    "unsupported_typed_uuid",
    "other_uuid_wrapper",
    "multiple_identifiers",
    "natural_language_reference",
    "malformed_reference",
    "other",
)
PROPOSAL_DISPOSITIONS = (
    "fully_eligible_and_schema_valid",
    "unsupported_reference_kind",
    "ambiguous_reference_syntax",
    "mixed_eligible_and_ineligible_references",
    "other_schema_failure",
    "unclassified",
)


def reference_category(value: object) -> str:
    """Classify one reference without disclosing its text or identifier."""
    try:
        parsed = parse_reference(value)
    except ReferenceSyntaxError:
        parsed = None
    if parsed is not None:
        return "canonical_uuid" if parsed.kind is None else "supported_typed_uuid"
    if not isinstance(value, str):
        return "other"
    fragments = _UUID_FRAGMENT.findall(value)
    if len(fragments) > 1:
        return "multiple_identifiers"
    if len(fragments) == 1:
        if _TYPED.fullmatch(value):
            return "unsupported_typed_uuid"
        return "other_uuid_wrapper"
    if any(character.isalpha() for character in value):
        return "natural_language_reference"
    return "malformed_reference"


def _other_schema_failure(arguments: dict[str, object]) -> bool:
    try:
        StartExperimentArgs.model_validate(arguments)
    except ValidationError as error:
        return any(item["loc"][0] != "target_objects" for item in error.errors())
    return False


def classify_proposals(
    rows: object, *, require_historical_counts: bool = True
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Return public aggregate and private ordinal rows; no mutation or lookup."""
    if not isinstance(rows, list):
        raise ValueError("proposal rows must be a list")
    if require_historical_counts and len(rows) != 18:
        raise ValueError("historical proposal count drift")
    dispositions: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    issue_flags: Counter[str] = Counter()
    private_rows: list[dict[str, object]] = []
    strict_count = eligible_count = schema_count = reference_count = 0
    context_observed_count = unknown_public_provenance_count = 0
    unknown_object_resolution_count = unknown_correspondence_count = 0
    for ordinal, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("arguments"), dict):
            raise ValueError("malformed proposal")
        arguments = row["arguments"]
        targets = arguments.get("target_objects")
        retained = row.get("references")
        if (
            not isinstance(targets, list)
            or not isinstance(retained, list)
            or len(targets) != len(retained)
            or any(not isinstance(item, dict) for item in retained)
        ):
            raise ValueError("proposal reference alignment failed")
        replay = replay_start_arguments(arguments)
        strict_count += replay.strict_schema_accepted
        eligible_count += replay.normalization_eligible
        schema_count += replay.post_normalization_schema_accepted
        reference_count += len(targets)
        context_observed_count += sum(item.get("context_observed") is True for item in retained)
        unknown_public_provenance_count += sum(
            item.get("public_source_grounded") is None for item in retained
        )
        unknown_object_resolution_count += sum(
            item.get("object_resolved") is None for item in retained
        )
        unknown_correspondence_count += row.get("would_support_correct_experiment") is None
        classified = tuple(reference_category(value) for value in targets)
        categories.update(classified)
        accepted = sum(kind in {"canonical_uuid", "supported_typed_uuid"} for kind in classified)
        rejected = len(classified) - accepted
        has_unsupported = "unsupported_typed_uuid" in classified
        has_ambiguous = any(
            kind in {"other_uuid_wrapper", "multiple_identifiers", "malformed_reference", "other"}
            for kind in classified
        )
        mixed = accepted > 0 and rejected > 0
        other_schema = _other_schema_failure(arguments)
        if replay.normalization_eligible and replay.post_normalization_schema_accepted:
            disposition = "fully_eligible_and_schema_valid"
        elif replay.normalization_eligible and other_schema:
            disposition = "other_schema_failure"
        elif mixed:
            disposition = "mixed_eligible_and_ineligible_references"
        elif has_unsupported:
            disposition = "unsupported_reference_kind"
        elif rejected:
            disposition = "ambiguous_reference_syntax"
        else:
            disposition = "unclassified"
        dispositions[disposition] += 1
        flags = {
            "unsupported_kind": has_unsupported,
            "invalid_wrapper": "other_uuid_wrapper" in classified,
            "multiple_references_with_invalid_entry": len(targets) > 1 and rejected > 0,
            "natural_language_reference": "natural_language_reference" in classified,
            "additional_schema_failure": other_schema,
            "unknown_reason": disposition == "unclassified",
        }
        issue_flags.update(name for name, present in flags.items() if present)
        context_observed = any(item.get("context_observed") is True for item in retained)
        public_provenance = (
            True if any(item.get("public_source_grounded") is True for item in retained) else None
        )
        correspondence = row.get("would_support_correct_experiment")
        private_rows.append(
            {
                "ordinal": ordinal,
                "proposal_id": row.get("tool_call_id"),
                "reference_count": len(targets),
                "accepted_reference_count": accepted,
                "rejected_reference_count": rejected,
                "reference_categories": classified,
                "unsupported_reference": has_unsupported,
                "ambiguous_reference": has_ambiguous,
                "mixed_eligible_and_ineligible": mixed,
                "normalization_eligible": replay.normalization_eligible,
                "normalized_schema_accepted": replay.post_normalization_schema_accepted,
                "other_schema_failure": other_schema,
                "context_observed_identifier": context_observed,
                "independently_verified_public_provenance": public_provenance,
                "object_correspondence_known": correspondence,
                "disposition": disposition,
                "issue_flags": flags,
            }
        )
    accepted_references = categories["canonical_uuid"] + categories["supported_typed_uuid"]
    if require_historical_counts and (
        reference_count != 43
        or accepted_references != 27
        or strict_count != 0
        or eligible_count != 4
        or schema_count != 4
        or context_observed_count != 35
        or unknown_public_provenance_count != 43
        or unknown_object_resolution_count != 43
        or unknown_correspondence_count != 18
    ):
        raise ValueError("pinned historical replay count drift")
    public = {
        "milestone": "M6.8.1.1",
        "scope": "read_only_historical_reference_failure_taxonomy",
        "proposal_count": len(rows),
        "strict_schema_accepted_proposals": strict_count,
        "normalization_eligible_proposals": eligible_count,
        "normalized_schema_accepted_proposals": schema_count,
        "reference_count": reference_count,
        "frozen_grammar_accepted_references": accepted_references,
        "proposal_dispositions": {name: dispositions[name] for name in PROPOSAL_DISPOSITIONS},
        "proposal_issue_flags_nonexclusive": {
            name: issue_flags[name] for name in sorted(issue_flags)
        },
        "reference_categories": {name: categories[name] for name in REFERENCE_CATEGORIES},
        "context_observed_reference_count": context_observed_count,
        "unknown_public_provenance_reference_count": unknown_public_provenance_count,
        "unknown_object_resolution_reference_count": unknown_object_resolution_count,
        "unknown_target_correspondence_proposal_count": unknown_correspondence_count,
        "independently_verified_public_provenance": None,
        "object_correspondence": None,
        "correct_target_selection": None,
        "provider_calls": 0,
    }
    return public, private_rows
