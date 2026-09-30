"""Small per-agent cache of recently acted-on entities and their exact evidence."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import EntityRef, WorldFact

_WORKSPACE = re.compile(r"^/api/workspaces/([^/]+)/(?:documents|invoices|tickets)$")
_DETAIL = re.compile(r"^/api/(documents|invoices|support/tickets)/([^/]+)$")
_PREVIEW = re.compile(r"^/api/public/invoices/([^/]+)/preview$")
_REFUND = re.compile(r"^/api/invoices/([^/]+)/refund$")
_TYPES = {"documents": "document", "invoices": "invoice", "support/tickets": "ticket"}


def _ref(kind: str, raw: str) -> EntityRef | None:
    try:
        return EntityRef(entity_type=kind, entity_id=UUID(raw))
    except ValueError:
        return None


def action_target(request: ActionRequest) -> EntityRef | None:
    """Identify the one resource explicitly addressed by a synthetic API action."""
    if request.method == "GET" and request.path == "/api/me":
        return (
            EntityRef(entity_type="identity", entity_id=request.identity_id)
            if request.identity_id
            else None
        )
    if request.method == "GET" and (match := _WORKSPACE.fullmatch(request.path)):
        return _ref("workspace", match.group(1))
    if request.method == "GET" and (match := _DETAIL.fullmatch(request.path)):
        return _ref(_TYPES[match.group(1)], match.group(2))
    if request.method == "GET" and (match := _PREVIEW.fullmatch(request.path)):
        return _ref("invoice", match.group(1))
    if request.method == "POST" and (match := _REFUND.fullmatch(request.path)):
        return _ref("invoice", match.group(1))
    return None


@dataclass(frozen=True)
class EvidenceSnapshot:
    target: EntityRef
    action_id: UUID
    evidence_id: UUID
    method: str
    path: str
    identity_id: UUID | None
    http_status: int
    facts: tuple[WorldFact, ...]


class ActiveWorkingSet:
    """Bounded recent entity/action pairs; older facts remain in the event ledger."""

    def __init__(self, *, max_entities: int = 5, max_actions_per_entity: int = 4) -> None:
        if not 1 <= max_entities <= 10 or not 1 <= max_actions_per_entity <= 6:
            raise ValueError("working-set limits are outside supported bounds")
        self.max_entities = max_entities
        self.max_actions_per_entity = max_actions_per_entity
        self._entries: OrderedDict[tuple[str, UUID], list[EvidenceSnapshot]] = OrderedDict()

    def observe(
        self,
        request: ActionRequest,
        result: ActionResult,
        facts: tuple[WorldFact, ...],
    ) -> None:
        target = action_target(request)
        if (
            target is None
            or result.status != "completed"
            or result.evidence_id is None
            or result.http_status is None
        ):
            return
        if result.action_id != request.action_id or any(
            fact.run_id != request.run_id
            or fact.source_action_ids != (request.action_id,)
            or fact.evidence_ids != (result.evidence_id,)
            for fact in facts
        ):
            raise ValueError("working-set facts do not match the gateway action")
        key = (target.entity_type, target.entity_id)
        history = self._entries.setdefault(key, [])
        history.append(
            EvidenceSnapshot(
                target=target,
                action_id=request.action_id,
                evidence_id=result.evidence_id,
                method=request.method,
                path=request.path,
                identity_id=request.identity_id,
                http_status=result.http_status,
                facts=tuple(fact for fact in facts if fact.subject == target),
            )
        )
        del history[: -self.max_actions_per_entity]
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entities:
            self._entries.popitem(last=False)

    def snapshot(self) -> tuple[tuple[EntityRef, tuple[EvidenceSnapshot, ...]], ...]:
        return tuple(
            (history[-1].target, tuple(history)) for history in reversed(self._entries.values())
        )

    def action_ids(self) -> tuple[UUID, ...]:
        return tuple(action.action_id for _, history in self.snapshot() for action in history)

    def entity_ids(self) -> set[UUID]:
        return {entity.entity_id for entity, _ in self.snapshot()}

    def render(self, *, max_chars: int, max_facts: int) -> tuple[list[str], tuple[UUID, ...]]:
        """Render evidence under its target and action, including historical states."""
        if not self._entries or max_chars < 100 or max_facts < 1:
            return [], ()
        lines = ["Active entity/evidence working set (recent gateway actions):"]
        selected: list[UUID] = []
        priority = {
            "body_excerpt": 8,
            "mentions_invoice": 7,
            "status": 7,
            "role": 6,
            "member_of": 6,
            "workspace": 6,
            "public_billing_email": 6,
            "title": 5,
        }

        def append(line: str) -> bool:
            if len("\n".join((*lines, line))) > max_chars:
                return False
            lines.append(line)
            return True

        for entity, history in self.snapshot():
            if not append(f"- {entity.entity_type}:{entity.entity_id}"):
                break
            for action in history:
                identity = str(action.identity_id) if action.identity_id else "anonymous"
                line = (
                    f"  action={action.action_id} evidence={action.evidence_id} "
                    f"identity={identity} {action.method} {action.path} "
                    f"HTTP {action.http_status}"
                )
                if not append(line):
                    break
                ranked = sorted(
                    action.facts,
                    key=lambda fact: priority.get(fact.predicate, 1),
                    reverse=True,
                )
                for fact in ranked:
                    if len(selected) >= max_facts:
                        break
                    predicate = fact.predicate
                    value = (
                        f"{fact.object_value.entity_type}:{fact.object_value.entity_id}"
                        if isinstance(fact.object_value, EntityRef)
                        else json.dumps(fact.object_value, ensure_ascii=False)
                    )
                    if (
                        predicate == "body_excerpt"
                        and isinstance(fact.object_value, str)
                        and len(fact.object_value) > 220
                    ):
                        predicate = "body_excerpt_prefix"
                        value = json.dumps(fact.object_value[:220], ensure_ascii=False)
                    if not append(f"    {predicate}={value}"):
                        break
                    selected.append(fact.fact_id)
        return lines, tuple(selected)
