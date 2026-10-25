"""Static capability comparison of frozen A1–A4 task contracts."""

from __future__ import annotations

from offsecgym.benchmark.manifests import make_task_manifest, source_pins
from offsecgym.benchmark.registry import get_archetype
from offsecgym.benchmark.tasks import canonical_sha256
from offsecgym.runtime.saas import fixture_for_seed_v2

AUDIT_VERSION = "r0-task-diversity-v1"
CAPABILITY_VERSION = "r0-capability-taxonomy-v1"

CAPABILITIES = {
    "A1": (
        "public_navigation",
        "identity_reasoning",
        "resource_relationship_inference",
        "authorization_reasoning",
        "evidence_synthesis",
    ),
    "A2": (
        "public_navigation",
        "identity_reasoning",
        "resource_relationship_inference",
        "authorization_reasoning",
        "evidence_synthesis",
    ),
    "A3": (
        "public_navigation",
        "identity_reasoning",
        "resource_relationship_inference",
        "authorization_reasoning",
        "evidence_synthesis",
    ),
    "A4": ("public_navigation", "resource_relationship_inference", "evidence_synthesis"),
}
OBJECTS = {"A1": "document", "A2": "invoice", "A3": "support_ticket", "A4": "invoice_metadata"}
CLUES = {
    "A1": "document.reference_document_id",
    "A2": "document_or_ticket_body_invoice_reference",
    "A3": "document.reference_ticket_id",
    "A4": "document_or_ticket_body_invoice_reference",
}


def diversity_audit(seed: int = 42) -> dict[str, object]:
    fixture = fixture_for_seed_v2(seed)
    documents = fixture["documents"]
    tickets = fixture["tickets"]
    task_rows: list[dict[str, object]] = []
    for archetype_id in ("A1", "A2", "A3", "A4"):
        manifest = make_task_manifest(
            archetype_id, task_mode="scoped_audit", variant="vulnerable", seed=seed
        )
        archetype = get_archetype(archetype_id)
        if archetype_id == "A1":
            clue_count = sum(bool(row.get("reference_document_id")) for row in documents)
        elif archetype_id == "A3":
            clue_count = sum(bool(row.get("reference_ticket_id")) for row in documents)
        else:
            clue_count = sum(
                "invoice" in str(row.get("body", "")).lower() for row in (*documents, *tickets)
            )
        task_rows.append(
            {
                "archetype_id": archetype_id,
                "task_id": manifest.task.task_id,
                "security_property_family": archetype.capability,
                "object_type": OBJECTS[archetype_id],
                "public_clue_class": CLUES[archetype_id],
                "configured_clue_count": clue_count,
                "capabilities_hypothesized": CAPABILITIES[archetype_id],
                "identity_context": "workspace_member"
                if archetype_id != "A4"
                else "anonymous_public_view",
                "state_mutation_required": False,
                "public_reachability": "qualified_A1_only"
                if archetype_id == "A1"
                else "not_evaluated",
                "difficulty_calibrated": False,
            }
        )
    overlap = [
        {
            "pair": [left["archetype_id"], right["archetype_id"]],
            "same_clue_class": left["public_clue_class"] == right["public_clue_class"],
            "shared_hypothesized_capabilities": sorted(
                set(left["capabilities_hypothesized"]) & set(right["capabilities_hypothesized"])
            ),
            "same_security_property_family": left["security_property_family"]
            == right["security_property_family"],
        }
        for index, left in enumerate(task_rows)
        for right in task_rows[index + 1 :]
    ]
    result: dict[str, object] = {
        "audit_version": AUDIT_VERSION,
        "capability_taxonomy_version": CAPABILITY_VERSION,
        "scope": "static_frozen_task_design_only",
        "seed": seed,
        "tasks": task_rows,
        "overlap": overlap,
        "source_sha256": source_pins(),
        "new_archetypes_qualified": 0,
        "provider_calls": 0,
    }
    result["normalized_sha256"] = canonical_sha256(result)
    return result
