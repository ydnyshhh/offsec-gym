"""Audited, GET-only prerequisite enumeration for M6.2.2 worker comparisons."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from offsecgym.experiment.scripted import BoundGatewayTools
from offsecgym.interfaces import EventStore
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import AgentContext, EntityRef, WorldFact
from offsecgym.schemas.events import (
    PrerequisiteBootstrapCompleted,
    PrerequisiteBootstrapStarted,
)
from offsecgym.schemas.specs import BootstrapBudget
from offsecgym.solver.scripted import ExperimentInfrastructureError
from offsecgym.worldview import EventWorldState


def _normalized_fact(fact: WorldFact) -> tuple[object, ...]:
    value = fact.object_value
    if isinstance(value, EntityRef):
        value = (value.entity_type, str(value.entity_id))
    return (
        fact.kind,
        fact.subject.entity_type,
        str(fact.subject.entity_id),
        fact.predicate,
        value,
        fact.status,
    )


class PrerequisiteBootstrap:
    def __init__(self, events: EventStore) -> None:
        self.events = events
        self.world = EventWorldState(events)

    async def run(
        self, context: AgentContext, tools: BoundGatewayTools, budget: BootstrapBudget
    ) -> PrerequisiteBootstrapCompleted:
        if context.range is None:
            raise ValueError("bootstrap requires a visible range")
        identities = sorted(context.range.identity_ids)
        started = await self.events.append(
            PrerequisiteBootstrapStarted(
                run_id=context.run_id,
                actor="controller",
                budget=budget,
                visible_identity_count=len(identities),
            )
        )
        checked: list[tuple[str, str, str | None]] = []
        workspace_choices: dict[UUID, list[tuple[int, UUID]]] = {}

        async def read(path: str, identity_id: UUID) -> tuple[WorldFact, ...]:
            if len(checked) >= budget.max_actions or len(checked) >= budget.max_http_requests:
                raise ExperimentInfrastructureError("bootstrap_action_budget_exhausted")
            action = ActionRequest(
                run_id=context.run_id,
                source_phase="bootstrap",
                identity_id=identity_id,
                kind="http_request",
                destination="saas",
                method="GET",
                path=path,
            )
            result = await tools.execute(action)
            if (
                result.status != "completed"
                or result.http_status != 200
                or result.evidence_id is None
            ):
                raise ExperimentInfrastructureError(
                    f"bootstrap_request_failed:{result.reason_code or result.http_status}"
                )
            facts = await self.world.record_response(action, result)
            checked.append(("GET", path, str(identity_id)))
            return facts

        for identity_id in identities:
            facts = await read("/api/me", identity_id)
            role = next((fact.object_value for fact in facts if fact.predicate == "role"), None)
            workspace = next(
                (
                    fact.object_value.entity_id
                    for fact in facts
                    if fact.predicate == "member_of" and isinstance(fact.object_value, EntityRef)
                ),
                None,
            )
            if workspace is not None and role in {"member", "workspace_admin"}:
                priority = 0 if role == "workspace_admin" else 1
                workspace_choices.setdefault(workspace, []).append((priority, identity_id))
        for workspace_id in sorted(workspace_choices):
            identity_id = min(workspace_choices[workspace_id])[1]
            for collection in ("documents", "invoices", "tickets"):
                await read(f"/api/workspaces/{workspace_id}/{collection}", identity_id)

        facts = await self.world.query(context.run_id)
        normalized = sorted(
            (_normalized_fact(fact) for fact in facts),
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
        )
        snapshot = json.dumps(
            {"facts": normalized, "checked_actions": checked},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        entities: dict[str, set[UUID]] = {
            kind: set() for kind in ("identity", "workspace", "document", "invoice", "ticket")
        }
        for fact in facts:
            if fact.subject.entity_type in entities:
                entities[fact.subject.entity_type].add(fact.subject.entity_id)
            if (
                isinstance(fact.object_value, EntityRef)
                and fact.object_value.entity_type in entities
            ):
                entities[fact.object_value.entity_type].add(fact.object_value.entity_id)
        return await self.events.append(
            PrerequisiteBootstrapCompleted(
                run_id=context.run_id,
                actor="controller",
                causation_id=started.event_id,
                snapshot_hash=hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
                identity_count=len(entities["identity"]),
                workspace_count=len(entities["workspace"]),
                document_count=len(entities["document"]),
                invoice_count=len(entities["invoice"]),
                ticket_count=len(entities["ticket"]),
                action_count=len(checked),
                http_request_count=len(checked),
            )
        )
