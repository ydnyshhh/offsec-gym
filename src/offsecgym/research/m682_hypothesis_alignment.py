"""Mechanical public relationships; no keyword-based intent inference."""

from __future__ import annotations

from offsecgym.research.m682_object_resolution import PublicObject


def relationship_edges(
    identifiers: tuple[str | None, ...], objects: tuple[PublicObject, ...]
) -> tuple[dict[str, object], ...]:
    """Return only documented foreign-key links among proposed public objects."""
    selected = {identifier for identifier in identifiers if identifier is not None}
    edges: set[tuple[str, str, str, str]] = set()
    for source in objects:
        if source.identifier not in selected:
            continue
        for target_kind, target_id in source.relationships:
            if target_id in selected:
                edges.add((source.kind, source.identifier, target_kind, target_id))
    return tuple(
        {
            "source_kind": source_kind,
            "source_identifier": source_id,
            "target_kind": target_kind,
            "target_identifier": target_id,
        }
        for source_kind, source_id, target_kind, target_id in sorted(edges)
    )


def hypothesis_support(hypothesis: object, edges: tuple[dict[str, object], ...]) -> bool | None:
    """Historical free text has no authoritative predicate-to-edge contract."""
    _ = hypothesis, edges
    return None
