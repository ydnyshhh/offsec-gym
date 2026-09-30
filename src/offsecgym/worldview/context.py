"""Deterministic, bounded selection of relevant shared facts for model context."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

from pydantic import Field

from offsecgym.interfaces import EventStore
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.domain import WorldFact
from offsecgym.schemas.events import ContextRetrieved
from offsecgym.worldview.state import EventWorldState

_TERMS = re.compile(r"[a-z0-9_]+")
_STOP = {"a", "an", "and", "api", "for", "in", "of", "or", "the", "to", "test"}


def _trust(fact: WorldFact) -> int:
    # v3 observed/corroborated are legacy evidence links, not semantic verification.
    if fact.status == "validated":
        return 4
    if fact.status == "contradicted":
        return -2
    if fact.status in {"multi_evidence_linked", "corroborated"}:
        return 2
    if fact.status in {"evidence_linked", "observed"}:
        return 1
    return 0


def _display_status(fact: WorldFact) -> str:
    return {
        "observed": "evidence_linked",
        "corroborated": "multi_evidence_linked",
    }.get(fact.status, fact.status)


@dataclass
class _FactGroup:
    representative: WorldFact
    evidence_ids: set[UUID]
    count: int
    observation_count: int
    first_seen: str
    last_seen: str
    index: int


class WorldContext(StrictModel):
    run_id: UUID
    fact_ids: tuple[UUID, ...]
    text: str = Field(max_length=8000)


class WorldContextBuilder:
    def __init__(self, state: EventWorldState, events: EventStore) -> None:
        self.state = state
        self.events = events

    async def build(
        self,
        run_id: UUID,
        query: str,
        *,
        kind: str | None = None,
        max_facts: int = 12,
        max_chars: int = 6000,
    ) -> WorldContext:
        if not 1 <= max_facts <= 100 or not 200 <= max_chars <= 8000:
            raise ValueError("context limits are outside supported bounds")
        facts = list(await self.state.query(run_id, kind=kind))
        terms = set(_TERMS.findall(query.casefold())) - _STOP
        groups: dict[tuple[str, UUID, str, str], _FactGroup] = {}
        for index, fact in enumerate(facts):
            value = (
                fact.object_value.model_dump(mode="json")
                if hasattr(fact.object_value, "model_dump")
                else fact.object_value
            )
            key = (
                fact.subject.entity_type,
                fact.subject.entity_id,
                fact.predicate,
                json.dumps(value, sort_keys=True, ensure_ascii=False),
            )
            stamp = fact.created_at.isoformat()
            group = groups.get(key)
            if group is None:
                groups[key] = _FactGroup(
                    representative=fact,
                    evidence_ids=set(fact.evidence_ids),
                    count=1,
                    observation_count=int(fact.kind in {"observation", "relationship"}),
                    first_seen=stamp,
                    last_seen=stamp,
                    index=index,
                )
            else:
                group.evidence_ids.update(fact.evidence_ids)
                group.count += 1
                group.observation_count += int(fact.kind in {"observation", "relationship"})
                group.first_seen = min(group.first_seen, stamp)
                group.last_seen = max(group.last_seen, stamp)
                group.index = index
                if _trust(fact) > _trust(group.representative):
                    group.representative = fact
        ranked: list[tuple[int, int, float, int, _FactGroup]] = []
        for group in groups.values():
            fact = group.representative
            searchable = " ".join(
                (
                    fact.kind or "",
                    fact.subject.entity_type,
                    str(fact.subject.entity_id),
                    fact.predicate,
                    str(fact.object_value),
                )
            ).casefold()
            matched = sum(term in searchable for term in terms)
            ranked.append((matched, _trust(fact), fact.confidence, group.index, group))
        ranked.sort(key=lambda item: item[:4], reverse=True)
        if terms and any(matches for matches, *_ in ranked):
            seeds = [group for matches, _, _, _, group in ranked if matches]
        else:
            seeds = [group for _, _, _, _, group in ranked[:4]]
        selected: list[_FactGroup] = []
        seen: set[tuple[str, UUID, str, str]] = set()
        for seed in seeds:
            related = [seed] + [
                group
                for _, _, _, _, group in ranked
                if group.representative.subject == seed.representative.subject and group is not seed
            ]
            for group in related:
                fact = group.representative
                value = (
                    fact.object_value.model_dump(mode="json")
                    if hasattr(fact.object_value, "model_dump")
                    else fact.object_value
                )
                key = (
                    fact.subject.entity_type,
                    fact.subject.entity_id,
                    fact.predicate,
                    json.dumps(value, sort_keys=True, ensure_ascii=False),
                )
                if key not in seen and len(selected) < max_facts:
                    selected.append(group)
                    seen.add(key)
            if len(selected) >= max_facts:
                break
        lines = ["Relevant world facts (unvalidated claims remain labeled):"]
        kept: list[_FactGroup] = []
        for group in selected:
            fact = group.representative
            evidence = ",".join(str(item) for item in sorted(group.evidence_ids, key=str)[:5])
            conflicts = ",".join(str(item) for item in fact.contradicts_fact_ids[:2])
            value = (
                fact.object_value.model_dump(mode="json")
                if hasattr(fact.object_value, "model_dump")
                else fact.object_value
            )
            line = (
                f"- {fact.fact_id} kind={fact.kind} subject={fact.subject.entity_type}:"
                f"{fact.subject.entity_id} predicate={fact.predicate} "
                f"value={json.dumps(value, ensure_ascii=False)} "
                f"status={_display_status(fact)} confidence={fact.confidence:.2f} "
                f"claim_count={group.count} observation_count={group.observation_count} "
                f"first_seen={group.first_seen} "
                f"last_seen={group.last_seen} "
                f"evidence={evidence} conflicts={conflicts}"
            )
            if len("\n".join((*lines, line))) > max_chars:
                continue
            lines.append(line)
            kept.append(group)
        claims = [claim for claim in await self.state.coverage(run_id) if claim.status == "active"]
        for claim in claims[:3]:
            line = f"- active coverage: {claim.component}: {claim.objective}"
            if len("\n".join((*lines, line))) <= max_chars:
                lines.append(line)
        if not kept and not claims:
            lines.append("- none")
        rendered = "\n".join(lines)
        context = WorldContext(
            run_id=run_id,
            fact_ids=tuple(group.representative.fact_id for group in kept),
            text=rendered,
        )
        await self.events.append(
            ContextRetrieved(
                run_id=run_id,
                actor="worldstate",
                query_sha256=hashlib.sha256(
                    json.dumps(
                        {"query": query, "kind": kind}, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                rendered_sha256=hashlib.sha256(rendered.encode()).hexdigest(),
                selected_fact_ids=context.fact_ids,
                max_facts=max_facts,
            )
        )
        return context
