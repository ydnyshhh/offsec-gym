"""Deterministic, bounded selection of relevant shared facts for model context."""

from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID

from pydantic import Field

from offsecgym.interfaces import EventStore
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.domain import WorldFact
from offsecgym.schemas.events import ContextRetrieved
from offsecgym.worldview.state import EventWorldState

_TERMS = re.compile(r"[a-z0-9_]+")
_STOP = {"a", "an", "and", "api", "for", "in", "of", "or", "the", "to", "test"}


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
        ranked: list[tuple[int, int, WorldFact]] = []
        for index, fact in enumerate(facts):
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
            score = matched * 100 + round(fact.confidence * 10)
            if fact.status in {"contradicted", "hypothesized"}:
                score -= 3
            ranked.append((score, index, fact))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if terms and any(score >= 100 for score, _, _ in ranked):
            seeds = [fact for score, _, fact in ranked if score >= 100]
        else:
            seeds = [fact for _, _, fact in ranked[:4]]
        selected: list[WorldFact] = []
        seen: set[UUID] = set()
        for seed in seeds:
            related = [seed] + [
                fact
                for _, _, fact in ranked
                if fact.subject == seed.subject and fact.fact_id != seed.fact_id
            ]
            for fact in related:
                if fact.fact_id not in seen and len(selected) < max_facts:
                    selected.append(fact)
                    seen.add(fact.fact_id)
            if len(selected) >= max_facts:
                break
        lines = ["Relevant world facts (unvalidated claims remain labeled):"]
        kept: list[WorldFact] = []
        for fact in selected:
            value = (
                fact.object_value.model_dump(mode="json")
                if hasattr(fact.object_value, "model_dump")
                else fact.object_value
            )
            line = (
                f"- {fact.fact_id} kind={fact.kind} subject={fact.subject.entity_type}:"
                f"{fact.subject.entity_id} predicate={fact.predicate} "
                f"value={json.dumps(value, ensure_ascii=False)} "
                f"status={fact.status} confidence={fact.confidence:.2f} "
                f"evidence={','.join(str(item) for item in fact.evidence_ids)}"
            )
            if len("\n".join((*lines, line))) > max_chars:
                continue
            lines.append(line)
            kept.append(fact)
        claims = [claim for claim in await self.state.coverage(run_id) if claim.status == "active"]
        for claim in claims[:3]:
            line = f"- active coverage: {claim.component}: {claim.objective}"
            if len("\n".join((*lines, line))) <= max_chars:
                lines.append(line)
        if not kept and not claims:
            lines.append("- none")
        rendered = "\n".join(lines)
        context = WorldContext(
            run_id=run_id, fact_ids=tuple(fact.fact_id for fact in kept), text=rendered
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
