"""Single-controller worldview projected from the authoritative run event stream."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from offsecgym.interfaces import EventStore
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import CoverageClaim, WorldFact
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    CoverageClaimed,
    CoverageLeaseAcquired,
    CoverageLeaseReleased,
    CoverageUpdated,
    WorldFactAdjudicated,
    WorldFactStateChange,
    WorldFactSubmitted,
)
from offsecgym.worldview.extract import ResponseFactExtractor


class WorldStateIntegrityError(RuntimeError):
    """The authoritative event stream cannot be projected into a valid worldview."""


class EventWorldState:
    def __init__(self, events: EventStore) -> None:
        self.events = events
        self._locks: dict[UUID, asyncio.Lock] = {}
        self.extractor = ResponseFactExtractor()

    @asynccontextmanager
    async def _transaction(self, run_id: UUID):
        """Use the database run lock for shared state; local lock is for test stores."""
        transaction = getattr(self.events, "run_transaction", None)
        if transaction is not None:
            async with transaction(run_id) as writer:
                yield writer
        else:
            async with self._locks.setdefault(run_id, asyncio.Lock()):
                yield self.events

    async def submit_fact(self, fact: WorldFact) -> WorldFact:
        if fact.schema_version != "4" or fact.status != "hypothesized":
            raise ValueError("world facts must enter as v4 hypothesized claims")
        if fact.supersedes_fact_id is not None or fact.superseded_by_fact_id is not None:
            raise ValueError("model claims cannot supersede facts without controller validation")
        async with self._transaction(fact.run_id) as writer:
            history = await writer.read_run(fact.run_id)
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
            await writer.append(
                WorldFactSubmitted(run_id=fact.run_id, actor="worldstate", fact=fact)
            )
            return fact

    async def record_response(
        self, request: ActionRequest, result: ActionResult
    ) -> tuple[WorldFact, ...]:
        """Verify the gateway event and body hash before extracting observed facts."""
        if result.evidence_id is None:
            return ()
        history = await self.events.read_run(request.run_id)
        requested = next(
            (
                item
                for item in history
                if isinstance(item, ActionRequested)
                and item.action_id == request.action_id
                and item.schema_version == "2"
            ),
            None,
        )
        completed = next(
            (
                item
                for item in history
                if isinstance(item, ActionCompleted) and item.action_id == request.action_id
            ),
            None,
        )
        if (
            requested is None
            or completed is None
            or result.action_id != request.action_id
            or requested.destination != request.destination
            or requested.method != request.method
            or requested.identity_id != request.identity_id
            or requested.path_sha256 != hashlib.sha256(request.path.encode()).hexdigest()
            or requested.sequence_number >= completed.sequence_number
            or completed.evidence_id != result.evidence_id
            or completed.http_status != result.http_status
            or completed.response_sha256 is None
            or completed.response_sha256 != result.response_sha256
        ):
            raise ValueError("gateway response does not match completed action events")
        facts = []
        for extracted in self.extractor.extract(request, result):
            fact = WorldFact(
                fact_id=uuid4(),
                run_id=request.run_id,
                kind=extracted.kind,
                subject=extracted.subject,
                predicate=extracted.predicate,
                object_value=extracted.object_value,
                source_worker_id=request.worker_id,
                source_event_ids=(completed.event_id,),
                source_action_ids=(request.action_id,),
                evidence_ids=(result.evidence_id,),
                confidence=1.0,
                status="observed",
            )
            facts.append(await self._persist_controller_fact(fact))
        return tuple(facts)

    async def _persist_controller_fact(self, fact: WorldFact) -> WorldFact:
        """Persist a field already verified by ``record_response``."""
        if (
            fact.schema_version != "4"
            or fact.status != "observed"
            or fact.kind not in {"observation", "relationship"}
            or len(fact.source_action_ids) != 1
            or len(fact.evidence_ids) != 1
            or len(fact.source_event_ids) != 1
        ):
            raise ValueError("controller observation requires one completed response source")
        async with self._transaction(fact.run_id) as writer:
            history = await writer.read_run(fact.run_id)
            facts, _ = self._project(history)
            if fact.fact_id in facts:
                raise ValueError("world fact ID already exists in this run")
            self._check_provenance(fact, history)
            completed = next(
                (
                    item
                    for item in history
                    if isinstance(item, ActionCompleted)
                    and item.event_id == fact.source_event_ids[0]
                    and item.action_id == fact.source_action_ids[0]
                    and item.evidence_id == fact.evidence_ids[0]
                    and item.http_status == 200
                ),
                None,
            )
            if completed is None:
                raise ValueError("controller observation source must be a 200 response")
            completed_by_action = {
                item.action_id: item.sequence_number
                for item in history
                if isinstance(item, ActionCompleted)
            }
            source_sequence = completed.sequence_number
            multi_valued = fact.predicate in {
                "contains_document",
                "contains_invoice",
                "contains_ticket",
                "has_role_in_project",
            }
            peers = [
                item
                for item in facts.values()
                if item.schema_version == "4"
                and item.status == "observed"
                and item.subject == fact.subject
                and item.predicate == fact.predicate
                and (not multi_valued or item.object_value == fact.object_value)
                and len(item.source_action_ids) == 1
                and item.source_action_ids[0] in completed_by_action
            ]
            newer = [
                item
                for item in peers
                if completed_by_action[item.source_action_ids[0]] > source_sequence
            ]
            await writer.append(
                WorldFactSubmitted(run_id=fact.run_id, actor="controller", fact=fact)
            )
            if newer:
                latest = max(
                    newer,
                    key=lambda item: completed_by_action[item.source_action_ids[0]],
                )
                changes = (
                    WorldFactStateChange(
                        fact_id=fact.fact_id,
                        status="superseded",
                        reason_code="older_source_action_completed",
                        replacement_fact_id=latest.fact_id,
                    ),
                )
            else:
                older = [
                    item
                    for item in peers
                    if completed_by_action[item.source_action_ids[0]] < source_sequence
                ]
                changes = (
                    (
                        WorldFactStateChange(
                            fact_id=fact.fact_id,
                            status="observed",
                            reason_code="source_action_order_verified",
                        ),
                        *(
                            WorldFactStateChange(
                                fact_id=item.fact_id,
                                status="superseded",
                                reason_code="newer_source_action_completed",
                                replacement_fact_id=fact.fact_id,
                            )
                            for item in older
                        ),
                    )
                    if older
                    else ()
                )
            if changes:
                await writer.append(
                    WorldFactAdjudicated(
                        run_id=fact.run_id,
                        actor="controller",
                        fact_id=fact.fact_id,
                        changes=changes,
                    )
                )
            return self._project(await writer.read_run(fact.run_id))[0][fact.fact_id]

    async def adjudicate_fact(self, run_id: UUID, fact_id: UUID) -> WorldFact:
        async with self._transaction(run_id) as writer:
            history = await writer.read_run(run_id)
            facts, _ = self._project(history)
            fact = facts.get(fact_id)
            if fact is None:
                raise ValueError("world fact is absent from this run")
            if any(
                isinstance(item, WorldFactAdjudicated) and item.fact_id == fact_id
                for item in history
            ):
                return fact
            linked = bool(fact.evidence_ids) and fact.kind in {"observation", "relationship"}
            if linked:
                status = (
                    "multi_evidence_linked"
                    if len(set(fact.evidence_ids)) >= 2
                    else "evidence_linked"
                )
            else:
                status = "hypothesized"
            reason = "gateway_evidence_link_verified" if linked else "claim_only"
            changes: dict[UUID, WorldFactStateChange] = {}
            links: set[UUID] = set(fact.contradicts_fact_ids)
            matching_linked_ids: set[UUID] = set()
            conflicting_linked_ids: set[UUID] = set()
            peers = [
                peer
                for peer in facts.values()
                if peer.fact_id != fact_id
                and peer.subject == fact.subject
                and peer.predicate == fact.predicate
                and peer.status != "superseded"
            ]
            for peer in peers:
                if peer.schema_version == "4" and peer.status == "observed":
                    if peer.object_value != fact.object_value:
                        links.add(peer.fact_id)
                        status, reason = "contradicted", "conflicts_with_controller_observation"
                    continue
                if peer.object_value == fact.object_value:
                    if (
                        linked
                        and peer.evidence_ids
                        and len(set(fact.evidence_ids) | set(peer.evidence_ids)) >= 2
                        and peer.status
                        in {"evidence_linked", "multi_evidence_linked", "observed", "corroborated"}
                    ):
                        status, reason = "multi_evidence_linked", "distinct_matching_links"
                        matching_linked_ids.add(peer.fact_id)
                        changes[peer.fact_id] = WorldFactStateChange(
                            fact_id=peer.fact_id,
                            status="multi_evidence_linked",
                            reason_code="distinct_matching_links",
                        )
                    continue
                links.add(peer.fact_id)
                peer_linked = peer.status in {
                    "evidence_linked",
                    "multi_evidence_linked",
                    "observed",
                    "corroborated",
                    "validated",
                } or (peer.status == "contradicted" and bool(peer.evidence_ids))
                if linked and peer_linked:
                    conflicting_linked_ids.add(peer.fact_id)
                    status, reason = "contradicted", "conflicting_linked_claims"
                changes[peer.fact_id] = WorldFactStateChange(
                    fact_id=peer.fact_id,
                    status=(
                        "contradicted"
                        if linked and peer_linked and peer.status != "validated"
                        else peer.status
                    ),
                    reason_code="conflicting_claim_link",
                    contradicts_fact_ids=(fact_id,),
                )
            if conflicting_linked_ids:
                status, reason = "contradicted", "conflicting_linked_claims"
                for matching_id in matching_linked_ids:
                    changes[matching_id] = WorldFactStateChange(
                        fact_id=matching_id,
                        status="contradicted",
                        reason_code="matching_claim_has_conflicting_links",
                        contradicts_fact_ids=tuple(sorted(conflicting_linked_ids, key=str)),
                    )
            changes[fact_id] = WorldFactStateChange(
                fact_id=fact_id,
                status=status,
                reason_code=reason,
                contradicts_fact_ids=tuple(sorted(links, key=str)),
            )
            await writer.append(
                WorldFactAdjudicated(
                    run_id=run_id,
                    actor="worldstate",
                    fact_id=fact_id,
                    changes=tuple(changes.values()),
                )
            )
            updated, _ = self._project(await writer.read_run(run_id))
            return updated[fact_id]

    async def supersede_fact(
        self, run_id: UUID, old_fact_id: UUID, new_fact_id: UUID, reason_code: str
    ) -> WorldFact:
        """Controller-only disposition; it is not exposed as a model tool."""
        async with self._transaction(run_id) as writer:
            facts, _ = self._project(await writer.read_run(run_id))
            old, new = facts.get(old_fact_id), facts.get(new_fact_id)
            if old is None or new is None or old.fact_id == new.fact_id:
                raise ValueError("supersession requires two existing distinct facts")
            if old.subject != new.subject or old.predicate != new.predicate:
                raise ValueError("supersession requires a matching subject and predicate")
            if old.status == "validated" or new.status != "validated":
                raise ValueError("supersession requires an independently validated replacement")
            await writer.append(
                WorldFactAdjudicated(
                    run_id=run_id,
                    actor="controller",
                    fact_id=old_fact_id,
                    changes=(
                        WorldFactStateChange(
                            fact_id=old_fact_id,
                            status="superseded",
                            reason_code=reason_code,
                            replacement_fact_id=new_fact_id,
                        ),
                    ),
                )
            )
            return self._project(await writer.read_run(run_id))[0][old_fact_id]

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
        async with self._transaction(claim.run_id) as writer:
            _, claims = self._project(await writer.read_run(claim.run_id))
            if claim.claim_id in claims or any(
                item.status == "active"
                and item.component.casefold() == claim.component.casefold()
                and item.objective.casefold() == claim.objective.casefold()
                for item in claims.values()
            ):
                raise ValueError("coverage is already actively claimed")
            await writer.append(
                CoverageClaimed(run_id=claim.run_id, actor="worldstate", claim=claim)
            )
            await writer.append(
                CoverageLeaseAcquired(
                    run_id=claim.run_id,
                    actor="worldstate",
                    claim_id=claim.claim_id,
                    task_id=claim.task_id,
                    component=claim.component,
                    objective=claim.objective,
                )
            )
            return claim

    async def update_coverage(
        self, run_id: UUID, claim_id: UUID, task_id: UUID, status: str
    ) -> CoverageClaim:
        if status not in {"completed", "released"}:
            raise ValueError("coverage can only be completed or released")
        async with self._transaction(run_id) as writer:
            _, claims = self._project(await writer.read_run(run_id))
            claim = claims.get(claim_id)
            if claim is None or claim.task_id != task_id or claim.status != "active":
                raise ValueError("coverage claim is absent, closed, or owned by another task")
            await writer.append(
                CoverageUpdated(run_id=run_id, actor="worldstate", claim_id=claim_id, status=status)
            )
            await writer.append(
                CoverageLeaseReleased(
                    run_id=run_id,
                    actor="worldstate",
                    claim_id=claim_id,
                    task_id=task_id,
                    status=status,
                )
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
                    if change.replacement_fact_id is not None:
                        replacement = facts.get(change.replacement_fact_id)
                        if (
                            change.status != "superseded"
                            or replacement is None
                            or replacement.subject != current.subject
                            or replacement.predicate != current.predicate
                        ):
                            raise WorldStateIntegrityError("invalid world fact replacement link")
                    links = tuple(
                        sorted(
                            set(current.contradicts_fact_ids) | set(change.contradicts_fact_ids),
                            key=str,
                        )
                    )
                    facts[change.fact_id] = current.model_copy(
                        update={
                            "status": change.status,
                            "contradicts_fact_ids": links,
                            "superseded_by_fact_id": change.replacement_fact_id
                            or current.superseded_by_fact_id,
                        }
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
