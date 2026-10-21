"""Detached, no-model syntax qualification for proposed M6.8.1 references.

Nothing in this module is called by the agent/controller runtime. Acceptance here
does not imply public-source provenance, object resolution, or state creation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from offsecgym.solver.experiment_state import StartExperimentArgs

# These names are advertised by the enterprise range's public entity surface at
# the audited source commit. The alias "change" is deliberately absent.
PUBLIC_ENTITY_KINDS = frozenset(
    {
        "identity",
        "organization",
        "project",
        "role_assignment",
        "access_request",
        "change_request",
        "environment",
        "job",
        "audit_event",
    }
)

_CANONICAL_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_TYPED = re.compile(rf"(?P<kind>[a-z][a-z_]*):(?P<identifier>{_CANONICAL_UUID})\Z", re.ASCII)
_BARE = re.compile(rf"{_CANONICAL_UUID}\Z", re.ASCII)


class ReferenceSyntaxError(ValueError):
    """The proposed reference is outside the prespecified public grammar."""


@dataclass(frozen=True)
class ParsedReference:
    kind: str | None
    identifier: UUID

    @property
    def canonical_uuid(self) -> str:
        return str(self.identifier)


@dataclass(frozen=True)
class OfflineReplay:
    strict_schema_accepted: bool
    normalization_eligible: bool
    post_normalization_schema_accepted: bool
    normalized_uuids: tuple[str, ...] | None
    declared_kinds: tuple[str | None, ...] | None


def parse_reference(value: object) -> ParsedReference:
    """Parse one exact public reference or canonical bare UUID, without lookup."""
    if not isinstance(value, str):
        raise ReferenceSyntaxError("unsupported_reference_syntax")
    if _BARE.fullmatch(value):
        identifier = UUID(value)
        if str(identifier) == value:
            return ParsedReference(None, identifier)
    match = _TYPED.fullmatch(value)
    if match is None or match["kind"] not in PUBLIC_ENTITY_KINDS:
        raise ReferenceSyntaxError("unsupported_reference_syntax")
    identifier = UUID(match["identifier"])
    if str(identifier) != match["identifier"]:
        raise ReferenceSyntaxError("noncanonical_uuid")
    return ParsedReference(match["kind"], identifier)


def _schema_accepts(arguments: object) -> bool:
    try:
        StartExperimentArgs.model_validate(arguments)
    except ValidationError:
        return False
    return True


def replay_start_arguments(arguments: object) -> OfflineReplay:
    """Compare schemas in memory; never dispatch, persist, or reflect arguments."""
    strict = _schema_accepts(arguments)
    if not isinstance(arguments, dict):
        return OfflineReplay(strict, False, strict, None, None)
    targets = arguments.get("target_objects")
    if not isinstance(targets, list) or not targets:
        return OfflineReplay(strict, False, strict, None, None)
    try:
        parsed = tuple(parse_reference(value) for value in targets)
    except ReferenceSyntaxError:
        return OfflineReplay(strict, False, strict, None, None)
    normalized = tuple(item.canonical_uuid for item in parsed)
    candidate = {**arguments, "target_objects": list(normalized)}
    return OfflineReplay(
        strict_schema_accepted=strict,
        normalization_eligible=True,
        post_normalization_schema_accepted=_schema_accepts(candidate),
        normalized_uuids=normalized,
        declared_kinds=tuple(item.kind for item in parsed),
    )
