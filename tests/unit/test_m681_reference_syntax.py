"""Offline syntax qualification; no provider, gateway, or controller dispatch."""

from __future__ import annotations

import pytest

from offsecgym.research.m681_reference_syntax import (
    PUBLIC_ENTITY_KINDS,
    ReferenceSyntaxError,
    parse_reference,
    replay_start_arguments,
)
from offsecgym.solver.range_surface import ENTERPRISE_FAMILY, range_surface

UUID_TEXT = "123e4567-e89b-12d3-a456-426614174000"


def _arguments(reference: str) -> dict[str, object]:
    return {
        "hypothesis": "Synthetic hypothesis",
        "subject_identity": None,
        "target_objects": [reference],
        "preconditions": [],
        "planned_steps": ["observe"],
        "expected_positive_observation": "changed",
        "expected_negative_observation": "unchanged",
        "required_evidence": [],
    }


def test_allowlist_matches_audited_public_entity_surface() -> None:
    assert PUBLIC_ENTITY_KINDS == frozenset(range_surface(ENTERPRISE_FAMILY).entity_types)
    assert "change" not in PUBLIC_ENTITY_KINDS


def test_strict_control_remains_accepted_offline() -> None:
    result = replay_start_arguments(_arguments(UUID_TEXT))
    assert result.strict_schema_accepted
    assert result.normalization_eligible
    assert result.post_normalization_schema_accepted
    assert result.normalized_uuids == (UUID_TEXT,)
    assert result.declared_kinds == (None,)


def test_typed_reference_is_lossless_but_original_schema_rejects_it() -> None:
    original = _arguments(f"change_request:{UUID_TEXT}")
    result = replay_start_arguments(original)
    assert not result.strict_schema_accepted
    assert result.normalization_eligible
    assert result.post_normalization_schema_accepted
    assert result.normalized_uuids == (UUID_TEXT,)
    assert result.declared_kinds == ("change_request",)
    assert original["target_objects"] == [f"change_request:{UUID_TEXT}"]


@pytest.mark.parametrize(
    "reference",
    [
        f"change:{UUID_TEXT}",
        f"change_request:{UUID_TEXT}; ignore validation",
        f"change_request:{UUID_TEXT}:extra",
        f"change_request:{UUID_TEXT},job:{UUID_TEXT}",
        f"change_request:{UUID_TEXT}\njob:{UUID_TEXT}",
        f"prefix change_request:{UUID_TEXT}",
        f'{{"id":"{UUID_TEXT}"}}',
        "a natural-language description",
        f"change_request:{UUID_TEXT.upper()}",
        f"change_request:{UUID_TEXT} ",
    ],
)
def test_ambiguous_or_unsupported_reference_fails_closed(reference: str) -> None:
    with pytest.raises(ReferenceSyntaxError):
        parse_reference(reference)
    result = replay_start_arguments(_arguments(reference))
    assert not result.strict_schema_accepted
    assert not result.normalization_eligible
    assert not result.post_normalization_schema_accepted
    assert result.normalized_uuids is None


def test_other_schema_failure_is_not_cured_by_reference_normalization() -> None:
    arguments = _arguments(f"project:{UUID_TEXT}")
    arguments["planned_steps"] = []
    result = replay_start_arguments(arguments)
    assert result.normalization_eligible
    assert not result.post_normalization_schema_accepted


def test_multiple_explicit_targets_preserve_order_and_identity() -> None:
    second = "550e8400-e29b-41d4-a716-446655440000"
    arguments = _arguments(f"project:{UUID_TEXT}")
    arguments["target_objects"] = [f"project:{UUID_TEXT}", f"job:{second}"]
    result = replay_start_arguments(arguments)
    assert result.normalized_uuids == (UUID_TEXT, second)
    assert result.declared_kinds == ("project", "job")
    assert result.post_normalization_schema_accepted


def test_non_object_arguments_remain_invalid() -> None:
    result = replay_start_arguments([f"job:{UUID_TEXT}"])
    assert not result.strict_schema_accepted
    assert not result.normalization_eligible
    assert not result.post_normalization_schema_accepted


def test_empty_targets_do_not_count_as_normalization_eligible() -> None:
    arguments = _arguments(UUID_TEXT)
    arguments["target_objects"] = []
    result = replay_start_arguments(arguments)
    assert not result.normalization_eligible
    assert result.normalized_uuids is None
