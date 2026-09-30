"""Versioned, exact aliases for replaying M5-v1 finding categories."""

from __future__ import annotations

from offsecgym.schemas.domain import CandidateFinding

LEGACY_M5V1_ALIASES: dict[str, dict[str, str]] = {
    "object_relation": {
        "different_workspace (no membership)": "foreign_workspace",
        "different_workspace (no membership, not support staff)": "foreign_workspace",
        "cross_tenant (different workspace than caller)": "foreign_workspace",
        "cross_tenant (ticket owned by a different workspace)": "foreign_workspace",
        "cross_workspace (document.workspace_id != subject.workspace_id)": "foreign_workspace",
        "cross_workspace (invoice.workspace_id != subject.workspace_id)": "foreign_workspace",
        "cross_workspace (ticket.workspace_id != subject.workspace_id)": "foreign_workspace",
        "own_workspace invoice": "own_workspace",
        "same workspace as caller but insufficient role": "own_workspace",
        "same_workspace": "own_workspace",
    },
    "resource_type": {"support_ticket": "ticket"},
}


def normalize_legacy_m5v1(finding: CandidateFinding) -> tuple[CandidateFinding, dict[str, str]]:
    """Change only documented M5-v1 aliases; never infer a hidden property or role."""
    expectation = finding.security_property.model_dump(mode="python")
    changes: dict[str, str] = {}
    for field, aliases in LEGACY_M5V1_ALIASES.items():
        current = expectation.get(field)
        if isinstance(current, str) and current in aliases:
            replacement = aliases[current]
            expectation[field] = replacement
            changes[field] = f"{current} -> {replacement}"
    normalized = finding.model_copy(
        update={"security_property": type(finding.security_property).model_validate(expectation)}
    )
    return normalized, changes
