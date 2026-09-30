"""Exact entity graph projected from controller-observed response facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from offsecgym.schemas.domain import EntityRef, FactValue, WorldFact


@dataclass
class LedgerEntity:
    ref: EntityRef
    attributes: dict[str, FactValue] = field(default_factory=dict)
    fact_ids: list[UUID] = field(default_factory=list)
    source_action_ids: list[UUID] = field(default_factory=list)
    evidence_ids: list[UUID] = field(default_factory=list)


class EntityLedger:
    def __init__(self, facts: list[WorldFact]) -> None:
        self.entities: dict[tuple[str, UUID], LedgerEntity] = {}
        for fact in facts:
            if fact.schema_version != "4" or fact.status != "observed":
                continue
            entry = self._entry(fact.subject)
            entry.attributes[fact.predicate] = fact.object_value
            entry.fact_ids.append(fact.fact_id)
            entry.source_action_ids.extend(fact.source_action_ids)
            entry.evidence_ids.extend(fact.evidence_ids)
            if isinstance(fact.object_value, EntityRef):
                self._entry(fact.object_value)

    def _entry(self, ref: EntityRef) -> LedgerEntity:
        key = (ref.entity_type, ref.entity_id)
        if key not in self.entities:
            self.entities[key] = LedgerEntity(ref=ref)
        return self.entities[key]

    def related_ids(self, seeds: set[UUID]) -> set[UUID]:
        """Expand one hop across observed entity relationships."""
        related = set(seeds)
        for entity in self.entities.values():
            if entity.ref.entity_id in seeds:
                related.update(
                    value.entity_id
                    for value in entity.attributes.values()
                    if isinstance(value, EntityRef)
                )
            elif any(
                isinstance(value, EntityRef) and value.entity_id in seeds
                for value in entity.attributes.values()
            ):
                related.add(entity.ref.entity_id)
        return related

    def as_dict(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for entity in sorted(
            self.entities.values(), key=lambda item: (item.ref.entity_type, str(item.ref.entity_id))
        ):
            result.append(
                {
                    "type": entity.ref.entity_type,
                    "id": str(entity.ref.entity_id),
                    "attributes": {
                        key: (
                            value.model_dump(mode="json") if isinstance(value, EntityRef) else value
                        )
                        for key, value in sorted(entity.attributes.items())
                    },
                    "fact_ids": [str(value) for value in entity.fact_ids],
                    "source_action_ids": [str(value) for value in entity.source_action_ids],
                    "evidence_ids": [str(value) for value in entity.evidence_ids],
                }
            )
        return result
