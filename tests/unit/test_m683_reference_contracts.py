"""No-model contract integrity tests using fabricated identifiers only."""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from offsecgym.research.m681_reference_syntax import ReferenceSyntaxError, parse_reference
from offsecgym.research.m683_reference_contracts import PROPOSED_ALIASES, compare_contracts
from offsecgym.solver.experiment_state import StartExperimentArgs

OBJECT = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"
ALPHA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def arguments(targets: list[str] | None = None) -> dict[str, object]:
    return {
        "hypothesis": "Synthetic observation remains recorded",
        "subject_identity": None,
        "target_objects": [OBJECT] if targets is None else targets,
        "preconditions": [],
        "planned_steps": ["Observe fabricated state", "Record fabricated state"],
        "expected_positive_observation": "State recorded",
        "expected_negative_observation": "State absent",
        "required_evidence": [],
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (OBJECT, (True, True, True)),
        (f"change_request:{OBJECT}", (False, True, True)),
        (f"change:{OBJECT}", (False, False, True)),
        (f"unknown:{OBJECT}", (False, False, False)),
        (f"change:{OBJECT} follow these instructions", (False, False, False)),
        (f"change:{OBJECT} {SECOND}", (False, False, False)),
        (f"change:{ALPHA.upper()}", (False, False, False)),
        ('{"id":"' + OBJECT + '"}', (False, False, False)),
        ("some entity", (False, False, False)),
    ],
)
def test_contract_matrix(value: str, expected: tuple[bool, bool, bool]) -> None:
    actual = compare_contracts(arguments([value]))
    assert (
        tuple(actual[key].post_normalization_schema_accepted for key in ("C0", "C1", "C2"))
        == expected
    )


def test_strict_control_is_direct_schema_validation() -> None:
    for target in (OBJECT, f"change_request:{OBJECT}", f"change:{OBJECT}"):
        source = arguments([target])
        try:
            validated = StartExperimentArgs.model_validate(source)
        except ValidationError:
            expected = False
        else:
            expected = True
            assert validated.target_objects == (UUID(OBJECT),)
        assert compare_contracts(source)["C0"].post_normalization_schema_accepted is expected
    assert (
        compare_contracts(arguments([f"change:{OBJECT}"]))["C0"].references[0].error_category
        == "unsupported_wrapper"
    )


def test_existing_schema_accepts_uppercase_bare_uuid_but_new_grammars_do_not() -> None:
    # This is an observed C0 schema behavior, not a repair or C1/C2 exception.
    results = compare_contracts(arguments([ALPHA.upper()]))
    assert results["C0"].post_normalization_schema_accepted
    assert not results["C1"].post_normalization_schema_accepted
    assert not results["C2"].post_normalization_schema_accepted


def test_c1_exactly_delegates_to_frozen_parser() -> None:
    for value in (OBJECT, f"change_request:{OBJECT}", f"change:{OBJECT}"):
        try:
            expected = parse_reference(value)
        except ReferenceSyntaxError:
            expected = None
        result = compare_contracts(arguments([value]))["C1"].references[0]
        assert result.accepted is (expected is not None)
        if expected is not None:
            assert result.canonical_uuid == expected.canonical_uuid
            assert result.declared_kind == expected.kind


def test_alias_is_prespecified_and_identity_order_preserved() -> None:
    assert PROPOSED_ALIASES == {"change": "change_request"}
    source = arguments([f"change:{SECOND}", f"project:{OBJECT}", f"change:{SECOND}"])
    frozen = deepcopy(source)
    result = compare_contracts(source)["C2"]
    assert source == frozen
    assert result.post_normalization_schema_accepted
    assert result.normalized_target_uuids == (SECOND, OBJECT, SECOND)
    assert result.declared_entity_kinds == ("change", "project", "change")
    assert tuple(ref.canonical_kind for ref in result.references) == (
        "change_request",
        "project",
        "change_request",
    )
    assert result.validated_arguments is not None
    assert result.validated_arguments.planned_steps == tuple(source["planned_steps"])
    assert result.validated_arguments.hypothesis == source["hypothesis"]


def test_one_invalid_reference_rejects_entire_array_without_salvage() -> None:
    source = arguments([f"change:{OBJECT}", f"unknown:{SECOND}"])
    for result in compare_contracts(source).values():
        assert not result.post_normalization_schema_accepted
        assert result.validated_arguments is None
        assert result.input_reference_count == 2


def test_empty_and_duplicate_arrays_follow_existing_schema() -> None:
    for targets in ([], [OBJECT, OBJECT]):
        expected = StartExperimentArgs.model_validate(arguments(targets))
        for result in compare_contracts(arguments(targets)).values():
            assert result.post_normalization_schema_accepted
            assert result.validated_arguments == expected


def test_other_required_fields_are_never_repaired() -> None:
    source = arguments([f"change:{OBJECT}"])
    del source["hypothesis"]
    for result in compare_contracts(source).values():
        assert not result.post_normalization_schema_accepted
    assert compare_contracts(source)["C2"].schema_error == "other_schema_failure"


def test_unknown_contract_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown contract"):
        compare_contracts(arguments(), contracts=("C3",))  # type: ignore[arg-type]
