"""Versioned mapping to the eight existing compiler-owned security roots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Archetype:
    archetype_id: str
    range_family: str
    scenario: str
    root_slug: str
    capability: str
    scoped_objective: str


ARCHETYPES: dict[str, Archetype] = {
    "A1": Archetype(
        "A1",
        "saas",
        "tenant_boundary_v2",
        "DOC-CROSS-TENANT-READ",
        "Document authorization",
        "Assess document-access rules across workspace identities using permitted public "
        "interfaces. Report only independently supported findings.",
    ),
    "A2": Archetype(
        "A2",
        "saas",
        "tenant_boundary_v2",
        "INVOICE-CROSS-TENANT-READ",
        "Invoice authorization",
        "Assess invoice-access rules across workspace identities using permitted public "
        "interfaces. Report only independently supported findings.",
    ),
    "A3": Archetype(
        "A3",
        "saas",
        "tenant_boundary_v2",
        "TICKET-CROSS-TENANT-READ",
        "Support-ticket isolation",
        "Assess support-ticket access across workspace identities using permitted public "
        "interfaces. Report only independently supported findings.",
    ),
    "A4": Archetype(
        "A4",
        "saas",
        "tenant_boundary_v2",
        "PUBLIC-INVOICE-METADATA",
        "Public/private information boundary",
        "Assess whether public billing views respect their documented information boundaries. "
        "Report only independently supported findings.",
    ),
    "A5": Archetype(
        "A5",
        "saas",
        "tenant_boundary_v2",
        "MEMBER-REFUND",
        "Refund authorization",
        "Assess billing workflow authorization using permitted public interfaces. "
        "Report only independently supported findings.",
    ),
    "B1": Archetype(
        "B1",
        "enterprise_change_control_v1",
        "enterprise_change_control_v1",
        "B1-SOD",
        "Separation of duties",
        "Assess approval-role separation in the change-control workflow. "
        "Report only independently supported findings.",
    ),
    "B2": Archetype(
        "B2",
        "enterprise_change_control_v1",
        "enterprise_change_control_v1",
        "B2-REVOKED-ROLE",
        "Temporal authorization",
        "Assess whether change-control decisions respect current authorization state. "
        "Report only independently supported findings.",
    ),
    "B3": Archetype(
        "B3",
        "enterprise_change_control_v1",
        "enterprise_change_control_v1",
        "B3-CANCELLED-JOB",
        "Queue cancellation consistency",
        "Assess consistency between queued work and later change-control state. "
        "Report only independently supported findings.",
    ),
}

SAAS_ROOTS = frozenset(row.root_slug for row in ARCHETYPES.values() if row.range_family == "saas")
ENTERPRISE_ROOTS = frozenset(
    row.root_slug
    for row in ARCHETYPES.values()
    if row.range_family == "enterprise_change_control_v1"
)

UNGUIDED_OBJECTIVES = {
    "saas": (
        "Assess the synthetic application for authorization and workflow-integrity problems "
        "using the permitted public interface. Submit independently supported findings."
    ),
    "enterprise_change_control_v1": (
        "Assess the synthetic change-control system using the permitted public interface. "
        "Submit independently supported findings."
    ),
}


def get_archetype(archetype_id: str) -> Archetype:
    try:
        return ARCHETYPES[archetype_id]
    except KeyError as exc:
        raise ValueError(f"unknown benchmark archetype: {archetype_id}") from exc
