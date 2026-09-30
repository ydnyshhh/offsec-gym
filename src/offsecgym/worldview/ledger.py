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
    facts: list[WorldFact] = field(default_factory=list)


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
            entry.facts.append(fact)
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

    def get_entity(
        self,
        entity_type: str,
        entity_id: UUID,
        *,
        history: list[WorldFact] | None = None,
        max_evidence_groups: int = 8,
    ) -> dict[str, object] | None:
        """Look up one exact entity with evidence kept under its own action."""
        entry = self.entities.get((entity_type, entity_id))
        if entry is None:
            return None
        if not 1 <= max_evidence_groups <= 20:
            raise ValueError("entity evidence group limit is outside supported bounds")
        source = history if history is not None else []
        groups: dict[tuple[UUID, UUID], list[WorldFact]] = {}
        for fact in source:
            if (
                fact.schema_version != "4"
                or fact.status not in {"observed", "superseded"}
                or fact.subject != entry.ref
                or len(fact.source_action_ids) != 1
                or len(fact.evidence_ids) != 1
            ):
                continue
            key = (fact.source_action_ids[0], fact.evidence_ids[0])
            groups.setdefault(key, []).append(fact)
        chosen = list(groups.items())
        if len(chosen) > max_evidence_groups:
            chosen = (
                [chosen[-1]]
                if max_evidence_groups == 1
                else [chosen[0], *chosen[-(max_evidence_groups - 1) :]]
            )
        evidence_groups = [
            {
                "action_id": str(action_id),
                "evidence_id": str(evidence_id),
                "fields": [
                    {
                        "predicate": fact.predicate,
                        "value": self._value(fact.object_value),
                        "status": fact.status,
                        "fact_id": str(fact.fact_id),
                    }
                    for fact in facts[:12]
                ],
            }
            for (action_id, evidence_id), facts in chosen
        ]
        return {
            "type": entity_type,
            "id": str(entity_id),
            "attributes": {
                key: self._value(value) for key, value in sorted(entry.attributes.items())
            },
            "evidence_groups": evidence_groups,
        }

    @staticmethod
    def _value(value: FactValue) -> object:
        return value.model_dump(mode="json") if isinstance(value, EntityRef) else value

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
                        key: self._value(value) for key, value in sorted(entity.attributes.items())
                    },
                    "fact_ids": [str(value) for value in entity.fact_ids],
                    "source_action_ids": [str(value) for value in entity.source_action_ids],
                    "evidence_ids": [str(value) for value in entity.evidence_ids],
                    "evidence_groups": self.get_entity(
                        entity.ref.entity_type,
                        entity.ref.entity_id,
                        history=entity.facts,
                    )["evidence_groups"],
                }
            )
        return result
