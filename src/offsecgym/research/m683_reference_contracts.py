"""Detached, no-model comparison of three experiment reference contracts.

The functions in this module do not dispatch tools, resolve objects, or persist
state. C1 delegates to the frozen M6.8.1 parser; C2 is opt-in and separate.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from offsecgym.research.m681_reference_syntax import (
    ReferenceSyntaxError,
    parse_reference,
)
from offsecgym.solver.experiment_state import StartExperimentArgs

ContractId = Literal["C0", "C1", "C2"]
CONTRACT_IDS: tuple[ContractId, ...] = ("C0", "C1", "C2")
PROPOSED_ALIASES = {"change": "change_request"}

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ALIAS = re.compile(rf"(?P<kind>change):(?P<identifier>{_UUID})\Z", re.ASCII)
_TYPED = re.compile(rf"(?P<kind>[a-z][a-z_]*):(?P<identifier>{_UUID})\Z", re.ASCII)
_FRAGMENT = re.compile(_UUID, re.IGNORECASE)
_UUID_SCHEMA = TypeAdapter(UUID)


class ContractSyntaxError(ValueError):
    """A reference is outside a prespecified detached contract."""


@dataclass(frozen=True)
class ReferenceResult:
    accepted: bool
    form: str
    error_category: str | None
    canonical_uuid: str | None = field(default=None, repr=False)
    declared_kind: str | None = None
    canonical_kind: str | None = None


@dataclass(frozen=True)
class ContractResult:
    contract_id: ContractId
    input_reference_count: int
    syntax_accepted: bool
    post_normalization_schema_accepted: bool
    normalized_target_uuids: tuple[str, ...] | None = field(repr=False)
    declared_entity_kinds: tuple[str | None, ...] | None = None
    normalization_error: str | None = None
    schema_error: str | None = None
    references: tuple[ReferenceResult, ...] = field(default=(), repr=False)
    validated_arguments: StartExperimentArgs | None = field(default=None, repr=False)


def _rejection_category(value: object) -> str:
    if not isinstance(value, str):
        return "other_representation_error"
    fragments = _FRAGMENT.findall(value)
    if len(fragments) > 1:
        return "multiple_uuids"
    if len(fragments) == 1:
        if _TYPED.fullmatch(value):
            return "unknown_kind"
        if value.startswith("{") or value.startswith("["):
            return "nested_encoded_json" if value.startswith("{") else "unsupported_wrapper"
        if value.startswith("(") or value.startswith("<"):
            return "unsupported_wrapper"
        return "extra_instructions"
    if ":" in value and any(character.isdigit() for character in value):
        return "malformed_uuid"
    if any(character.isalpha() for character in value):
        return "natural_language_reference"
    return "malformed_uuid"


def _reference(value: object, contract: ContractId) -> ReferenceResult:
    if contract == "C0":
        try:
            identifier = _UUID_SCHEMA.validate_python(value)
        except ValidationError:
            category = _rejection_category(value)
            if category == "unknown_kind":
                category = "unsupported_wrapper"
            return ReferenceResult(False, "rejected", category)
        return ReferenceResult(
            True,
            "canonical_uuid"
            if isinstance(value, str) and str(identifier) == value
            else "schema_uuid",
            None,
            str(identifier),
        )
    try:
        parsed = parse_reference(value)
    except ReferenceSyntaxError:
        parsed = None
    if parsed is not None:
        return ReferenceResult(
            True,
            "canonical_uuid" if parsed.kind is None else "canonical_typed_uuid",
            None,
            parsed.canonical_uuid,
            parsed.kind,
            parsed.kind,
        )
    if contract == "C2" and isinstance(value, str):
        match = _ALIAS.fullmatch(value)
        if match is not None:
            identifier = UUID(match["identifier"])
            if str(identifier) == match["identifier"]:
                return ReferenceResult(
                    True,
                    "proposed_alias",
                    None,
                    str(identifier),
                    match["kind"],
                    PROPOSED_ALIASES[match["kind"]],
                )
    return ReferenceResult(False, "rejected", _rejection_category(value))


def _schema_error(error: ValidationError) -> str:
    locations = [item["loc"] for item in error.errors()]
    target = any(location and location[0] == "target_objects" for location in locations)
    other = any(not location or location[0] != "target_objects" for location in locations)
    if target and other:
        return "target_and_other_schema_failure"
    if target:
        return "target_schema_failure"
    return "other_schema_failure"


def compare_contracts(
    arguments: object, contracts: tuple[ContractId, ...] = CONTRACT_IDS
) -> dict[ContractId, ContractResult]:
    """Validate complete arrays; preserve all fields, order, and input identity."""
    if any(contract not in CONTRACT_IDS for contract in contracts):
        raise ValueError("unknown contract")
    results: dict[ContractId, ContractResult] = {}
    for contract in contracts:
        candidate = deepcopy(arguments)
        raw_targets = candidate.get("target_objects") if isinstance(candidate, dict) else None
        array_valid = isinstance(raw_targets, (list, tuple))
        values = tuple(raw_targets) if array_valid else ()
        refs = tuple(_reference(value, contract) for value in values)
        syntax = array_valid and all(ref.accepted for ref in refs)
        normalization_error = (
            None if syntax else "invalid_target_array" if not array_valid else "rejected_reference"
        )
        if syntax and contract != "C0":
            candidate["target_objects"] = [ref.canonical_uuid for ref in refs]
        try:
            validated = StartExperimentArgs.model_validate(candidate) if syntax else None
            schema_error = None
        except ValidationError as error:
            validated = None
            schema_error = _schema_error(error)
        # The strict control must always attempt the original schema, even when
        # an individual target is invalid; its result is never repaired.
        if contract == "C0" and not syntax:
            try:
                StartExperimentArgs.model_validate(candidate)
            except ValidationError as error:
                schema_error = _schema_error(error)
        results[contract] = ContractResult(
            contract_id=contract,
            input_reference_count=len(values),
            syntax_accepted=syntax,
            post_normalization_schema_accepted=validated is not None,
            normalized_target_uuids=(
                tuple(str(value) for value in validated.target_objects)
                if validated is not None
                else tuple(ref.canonical_uuid for ref in refs)
                if syntax
                else None
            ),
            declared_entity_kinds=tuple(ref.declared_kind for ref in refs) if syntax else None,
            normalization_error=normalization_error,
            schema_error=schema_error,
            references=refs,
            validated_arguments=validated,
        )
    return results
