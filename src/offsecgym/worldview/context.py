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
from offsecgym.worldview.ledger import EntityLedger
from offsecgym.worldview.state import EventWorldState
from offsecgym.worldview.working_set import ActiveWorkingSet

_TERMS = re.compile(r"[a-z0-9_]+")
_UUIDS = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
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
        max_facts: int = 40,
        max_chars: int = 7500,
        working_set: ActiveWorkingSet | None = None,
    ) -> WorldContext:
        if not 1 <= max_facts <= 100 or not 200 <= max_chars <= 8000:
            raise ValueError("context limits are outside supported bounds")
        facts = list(await self.state.query(run_id, kind=kind))
        terms = set(_TERMS.findall(query.casefold())) - _STOP
        seed_ids = {UUID(item) for item in _UUIDS.findall(query)}
        if working_set is not None:
            seed_ids.update(working_set.entity_ids())
        ledger = EntityLedger(facts)
        related_ids = ledger.related_ids(seed_ids)
        unique_observed: dict[tuple[str, UUID, str, str], WorldFact] = {}
        other_facts: list[WorldFact] = []
        for fact in facts:
            if fact.schema_version == "4" and fact.status == "observed":
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
                unique_observed.pop(key, None)
                unique_observed[key] = fact
            else:
                other_facts.append(fact)
        controller_facts = list(unique_observed.values())
        priority = {
            "role": 6,
            "member_of": 6,
            "workspace": 5,
            "status": 5,
            "contains_invoice": 4,
            "contains_document": 4,
            "contains_ticket": 4,
            "username": 3,
            "body_excerpt": 4,
            "mentions_invoice": 5,
        }
        lines = ["Relevant world facts (unvalidated claims remain labeled):"]
        identity_fact_ids: tuple[UUID, ...] = ()
        working_lines: list[str] = []
        identity_lines: list[str] = []
        checked_lines: list[str] = []
        working_fact_ids: tuple[UUID, ...] = ()
        if working_set is not None:
            identity_lines, identity_fact_ids = working_set.render_identities(
                max_chars=min(1800, max_chars // 3)
            )
            checked_lines = working_set.render_checked_actions(
                max_chars=min(4500, max_chars * 3 // 5)
            )
            working_lines, working_fact_ids = working_set.render(
                max_chars=min(1000, max_chars // 4),
                max_facts=min(8, max_facts),
            )
            lines.extend(identity_lines)
            lines.extend(checked_lines)
            lines.extend(working_lines)
        working_fact_set = set(identity_fact_ids) | set(working_fact_ids)
        indexed = [
            (index, fact)
            for index, fact in enumerate(controller_facts)
            if fact.fact_id not in working_fact_set
        ]
        indexed.sort(
            key=lambda pair: (
                int(pair[1].subject.entity_id in seed_ids) * 10
                + int(pair[1].subject.entity_id in related_ids) * 5
                + int(
                    hasattr(pair[1].object_value, "entity_id")
                    and pair[1].object_value.entity_id in related_ids
                )
                * 5,
                priority.get(pair[1].predicate, 1),
                pair[0],
            ),
            reverse=True,
        )
        ledger_kept: list[WorldFact] = []
        if indexed:
            lines.append("Known entities (controller-observed; exact IDs):")
        ledger_limit = max_chars - (min(1400, max_chars // 3) if other_facts else 100)
        for _, fact in indexed:
            if (
                len(ledger_kept) + len(identity_fact_ids) + len(working_fact_ids) >= max_facts
                or ledger_limit <= 0
            ):
                break
            value = (
                f"{fact.object_value.entity_type}:{fact.object_value.entity_id}"
                if hasattr(fact.object_value, "entity_id")
                else json.dumps(fact.object_value, ensure_ascii=False)
            )
            source = str(fact.source_action_ids[0]) if fact.source_action_ids else "unknown"
            evidence = str(fact.evidence_ids[0]) if fact.evidence_ids else "unknown"
            line = (
                f"- {fact.subject.entity_type}:{fact.subject.entity_id} "
                f"{fact.predicate}={value} source_action={source} evidence={evidence}"
            )
            if len("\n".join((*lines, line))) > ledger_limit:
                continue
            lines.append(line)
            ledger_kept.append(fact)
        groups: dict[tuple[str, UUID, str, str], _FactGroup] = {}
        for index, fact in enumerate(other_facts):
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
            if fact.subject.entity_id in related_ids:
                matched += 3
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
                if (
                    key not in seen
                    and len(selected)
                    + len(ledger_kept)
                    + len(identity_fact_ids)
                    + len(working_fact_ids)
                    < max_facts
                ):
                    selected.append(group)
                    seen.add(key)
            if (
                len(selected) + len(ledger_kept) + len(identity_fact_ids) + len(working_fact_ids)
                >= max_facts
            ):
                break
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
        if (
            not kept
            and not ledger_kept
            and not claims
            and not identity_lines
            and not checked_lines
            and not working_lines
        ):
            lines.append("- none")
        rendered = "\n".join(lines)
        context = WorldContext(
            run_id=run_id,
            fact_ids=tuple(
                list(identity_fact_ids)
                + list(working_fact_ids)
                + [fact.fact_id for fact in ledger_kept]
                + [group.representative.fact_id for group in kept]
            ),
            text=rendered,
        )
        await self.events.append(
            ContextRetrieved(
                run_id=run_id,
                actor="worldstate",
                query_sha256=hashlib.sha256(
                    json.dumps(
                        {
                            "query": query,
                            "kind": kind,
                            "working_actions": (
                                [str(item) for item in working_set.action_ids()]
                                if working_set is not None
                                else []
                            ),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                rendered_sha256=hashlib.sha256(rendered.encode()).hexdigest(),
                selected_fact_ids=context.fact_ids,
                max_facts=max_facts,
            )
        )
        return context
