"""Single-controller worldview projected from the authoritative run event stream."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from offsecgym.interfaces import EventStore
from offsecgym.schemas.domain import CoverageClaim, WorldFact
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    CoverageClaimed,
    CoverageUpdated,
    WorldFactAdjudicated,
    WorldFactStateChange,
    WorldFactSubmitted,
)


class WorldStateIntegrityError(RuntimeError):
    """The authoritative event stream cannot be projected into a valid worldview."""


class EventWorldState:
    def __init__(self, events: EventStore) -> None:
        self.events = events
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def submit_fact(self, fact: WorldFact) -> WorldFact:
        if fact.schema_version != "3" or fact.status != "hypothesized":
            raise ValueError("world facts must enter as v3 hypothesized claims")
        async with self._locks.setdefault(fact.run_id, asyncio.Lock()):
            history = await self.events.read_run(fact.run_id)
            facts, _ = self._project(history)
            if fact.fact_id in facts:
                raise ValueError("world fact ID already exists in this run")
            self._check_provenance(fact, history)
            for related_id in (*fact.contradicts_fact_ids, fact.supersedes_fact_id):
                if related_id is None:
                    continue
                related = facts.get(related_id)
                if (
                    related is None
                    or related.subject != fact.subject
                    or related.predicate != fact.predicate
                ):
                    raise ValueError("related world fact is absent or has a different key")
                if (
                    related_id in fact.contradicts_fact_ids
                    and related.object_value == fact.object_value
                ):
                    raise ValueError("contradictory facts must have different values")
            await self.events.append(
                WorldFactSubmitted(run_id=fact.run_id, actor="worldstate", fact=fact)
            )
            return fact

    async def adjudicate_fact(self, run_id: UUID, fact_id: UUID) -> WorldFact:
        async with self._locks.setdefault(run_id, asyncio.Lock()):
            history = await self.events.read_run(run_id)
            facts, _ = self._project(history)
            fact = facts.get(fact_id)
            if fact is None:
                raise ValueError("world fact is absent from this run")
            if any(
                isinstance(item, WorldFactAdjudicated) and item.fact_id == fact_id
                for item in history
            ):
                return fact
            grounded = bool(fact.evidence_ids) and fact.kind in {"observation", "relationship"}
            status = "observed" if grounded else "hypothesized"
            reason = "gateway_evidence_verified" if grounded else "claim_only"
            changes: dict[UUID, WorldFactStateChange] = {}
            links: set[UUID] = set(fact.contradicts_fact_ids)
            peers = [
                peer
                for peer in facts.values()
                if peer.fact_id != fact_id
                and peer.subject == fact.subject
                and peer.predicate == fact.predicate
                and peer.status != "superseded"
            ]
            for peer in peers:
                if peer.object_value == fact.object_value:
                    if (
                        grounded
                        and peer.evidence_ids
                        and set(fact.evidence_ids).isdisjoint(peer.evidence_ids)
                        and peer.status in {"observed", "corroborated"}
                    ):
                        status, reason = "corroborated", "independent_matching_evidence"
                        changes[peer.fact_id] = WorldFactStateChange(
                            fact_id=peer.fact_id,
                            status="corroborated",
                            reason_code="independent_matching_evidence",
                        )
                    continue
                links.add(peer.fact_id)
                if (
                    fact.supersedes_fact_id == peer.fact_id
                    and grounded
                    and peer.status != "validated"
                ):
                    changes[peer.fact_id] = WorldFactStateChange(
                        fact_id=peer.fact_id,
                        status="superseded",
                        reason_code="evidence_backed_supersession",
                    )
                    continue
                if peer.status in {"observed", "corroborated", "validated"} or (
                    peer.status == "contradicted" and peer.evidence_ids
                ):
                    status, reason = "contradicted", "conflicting_prior_evidence"
                    if grounded and peer.status != "validated":
                        changes[peer.fact_id] = WorldFactStateChange(
                            fact_id=peer.fact_id,
                            status="contradicted",
                            reason_code="conflicting_verified_evidence",
                            contradicts_fact_ids=(fact_id,),
                        )
                elif grounded:
                    changes[peer.fact_id] = WorldFactStateChange(
                        fact_id=peer.fact_id,
                        status="contradicted",
                        reason_code="conflicted_by_gateway_evidence",
                        contradicts_fact_ids=(fact_id,),
                    )
            if fact.supersedes_fact_id is not None and not grounded:
                raise ValueError("a claim without gateway evidence cannot supersede a fact")
            changes[fact_id] = WorldFactStateChange(
                fact_id=fact_id,
                status=status,
                reason_code=reason,
                contradicts_fact_ids=tuple(sorted(links, key=str)),
            )
            await self.events.append(
                WorldFactAdjudicated(
                    run_id=run_id,
                    actor="worldstate",
                    fact_id=fact_id,
                    changes=tuple(changes.values()),
                )
            )
            updated, _ = self._project(await self.events.read_run(run_id))
            return updated[fact_id]

    async def query(
        self,
        run_id: UUID,
        predicate: str | None = None,
        *,
        kind: str | None = None,
        include_superseded: bool = False,
    ) -> Sequence[WorldFact]:
        facts, _ = self._project(await self.events.read_run(run_id))
        return tuple(
            fact
            for fact in facts.values()
            if (predicate is None or fact.predicate == predicate)
            and (kind is None or fact.kind == kind)
            and (include_superseded or fact.status != "superseded")
        )

    async def claim_coverage(self, claim: CoverageClaim) -> CoverageClaim:
        if claim.status != "active":
            raise ValueError("new coverage claim must be active")
        async with self._locks.setdefault(claim.run_id, asyncio.Lock()):
            _, claims = self._project(await self.events.read_run(claim.run_id))
            if claim.claim_id in claims or any(
                item.status == "active"
                and item.component.casefold() == claim.component.casefold()
                and item.objective.casefold() == claim.objective.casefold()
                for item in claims.values()
            ):
                raise ValueError("coverage is already actively claimed")
            await self.events.append(
                CoverageClaimed(run_id=claim.run_id, actor="worldstate", claim=claim)
            )
            return claim

    async def update_coverage(
        self, run_id: UUID, claim_id: UUID, task_id: UUID, status: str
    ) -> CoverageClaim:
        if status not in {"completed", "released"}:
            raise ValueError("coverage can only be completed or released")
        async with self._locks.setdefault(run_id, asyncio.Lock()):
            _, claims = self._project(await self.events.read_run(run_id))
            claim = claims.get(claim_id)
            if claim is None or claim.task_id != task_id or claim.status != "active":
                raise ValueError("coverage claim is absent, closed, or owned by another task")
            await self.events.append(
                CoverageUpdated(run_id=run_id, actor="worldstate", claim_id=claim_id, status=status)
            )
            return claim.model_copy(update={"status": status})

    async def coverage(self, run_id: UUID) -> Sequence[CoverageClaim]:
        _, claims = self._project(await self.events.read_run(run_id))
        return tuple(claims.values())

    @staticmethod
    def _check_provenance(fact: WorldFact, history: Sequence[object]) -> None:
        event_by_id = {item.event_id: item for item in history}
        if any(event_id not in event_by_id for event_id in fact.source_event_ids):
            raise ValueError("source event is absent from this run")
        requested = {
            item.action_id: item
            for item in history
            if isinstance(item, ActionRequested) and item.schema_version == "2"
        }
        completed = {item.action_id: item for item in history if isinstance(item, ActionCompleted)}
        for action_id in fact.source_action_ids:
            start = requested.get(action_id)
            end = completed.get(action_id)
            if start is None or end is None or start.sequence_number >= end.sequence_number:
                raise ValueError("source action lacks a completed same-run gateway trace")
        if fact.evidence_ids and not fact.source_action_ids:
            raise ValueError("evidence needs a matching source action")
        supported = {completed[action_id].evidence_id for action_id in fact.source_action_ids}
        if not set(fact.evidence_ids).issubset(supported):
            raise ValueError("evidence is not bound to the source actions")
        if fact.kind == "observation" and not fact.evidence_ids:
            raise ValueError("observations require gateway evidence")

    @staticmethod
    def _project(
        history: Sequence[object],
    ) -> tuple[dict[UUID, WorldFact], dict[UUID, CoverageClaim]]:
        facts: dict[UUID, WorldFact] = {}
        coverage: dict[UUID, CoverageClaim] = {}
        for event in history:
            if isinstance(event, WorldFactSubmitted):
                if event.fact.fact_id in facts:
                    raise WorldStateIntegrityError("duplicate world fact ID in event stream")
                facts[event.fact.fact_id] = event.fact
            elif isinstance(event, WorldFactAdjudicated):
                for change in event.changes:
                    current = facts.get(change.fact_id)
                    if current is None:
                        raise WorldStateIntegrityError(
                            "world fact adjudication references a missing fact"
                        )
                    links = tuple(
                        sorted(
                            set(current.contradicts_fact_ids) | set(change.contradicts_fact_ids),
                            key=str,
                        )
                    )
                    facts[change.fact_id] = current.model_copy(
                        update={"status": change.status, "contradicts_fact_ids": links}
                    )
            elif isinstance(event, CoverageClaimed):
                if event.claim.claim_id in coverage:
                    raise WorldStateIntegrityError("duplicate coverage claim ID in event stream")
                coverage[event.claim.claim_id] = event.claim
            elif isinstance(event, CoverageUpdated):
                current = coverage.get(event.claim_id)
                if current is None:
                    raise WorldStateIntegrityError("coverage update references a missing claim")
                coverage[event.claim_id] = current.model_copy(update={"status": event.status})
        return facts, coverage
