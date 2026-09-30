"""Small per-agent cache of recently acted-on entities and their exact evidence."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
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
    response_sha256: str | None
    request_fingerprint: str
    facts: tuple[WorldFact, ...]


class ActiveWorkingSet:
    """Bounded recent entity/action pairs; older facts remain in the event ledger."""

    def __init__(
        self,
        *,
        max_entities: int = 5,
        max_actions_per_entity: int = 4,
        max_identities: int = 16,
        max_checked_actions: int = 32,
    ) -> None:
        if (
            not 1 <= max_entities <= 10
            or not 1 <= max_actions_per_entity <= 6
            or not 1 <= max_identities <= 16
            or not 1 <= max_checked_actions <= 32
        ):
            raise ValueError("working-set limits are outside supported bounds")
        self.max_entities = max_entities
        self.max_actions_per_entity = max_actions_per_entity
        self.max_identities = max_identities
        self.max_checked_actions = max_checked_actions
        self._entries: OrderedDict[tuple[str, UUID], list[EvidenceSnapshot]] = OrderedDict()
        self._identities: OrderedDict[UUID, EvidenceSnapshot] = OrderedDict()
        self._checked: OrderedDict[str, EvidenceSnapshot] = OrderedDict()

    @staticmethod
    def fingerprint(request: ActionRequest) -> str:
        canonical = json.dumps(
            {
                "method": request.method,
                "path": request.path,
                "identity_id": str(request.identity_id) if request.identity_id else None,
                "body": request.json_body,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode()).hexdigest()

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
        snapshot = EvidenceSnapshot(
            target=target,
            action_id=request.action_id,
            evidence_id=result.evidence_id,
            method=request.method,
            path=request.path,
            identity_id=request.identity_id,
            http_status=result.http_status,
            response_sha256=result.response_sha256,
            request_fingerprint=self.fingerprint(request),
            facts=tuple(fact for fact in facts if fact.subject == target),
        )
        if target.entity_type == "identity":
            self._identities[target.entity_id] = snapshot
            self._identities.move_to_end(target.entity_id)
            while len(self._identities) > self.max_identities:
                self._identities.popitem(last=False)
            return
        history = self._entries.setdefault(key, [])
        history.append(snapshot)
        del history[: -self.max_actions_per_entity]
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entities:
            self._entries.popitem(last=False)
        self._checked[snapshot.request_fingerprint] = snapshot
        self._checked.move_to_end(snapshot.request_fingerprint)
        while len(self._checked) > self.max_checked_actions:
            self._checked.popitem(last=False)

    def snapshot(self) -> tuple[tuple[EntityRef, tuple[EvidenceSnapshot, ...]], ...]:
        return tuple(
            (history[-1].target, tuple(history)) for history in reversed(self._entries.values())
        )

    def action_ids(self) -> tuple[UUID, ...]:
        return tuple(
            [snapshot.action_id for snapshot in self._identities.values()]
            + [snapshot.action_id for snapshot in self._checked.values()]
        )

    def entity_ids(self) -> set[UUID]:
        return {entity.entity_id for entity, _ in self.snapshot()} | set(self._identities)

    def render_identities(self, *, max_chars: int) -> tuple[list[str], tuple[UUID, ...]]:
        if not self._identities:
            return [], ()
        lines = ["Checked identities (persistent role/workspace and citation):"]
        selected: list[UUID] = []
        for identity_id, snapshot in self._identities.items():
            fields = {fact.predicate: fact for fact in snapshot.facts}
            role = fields.get("role")
            workspace = fields.get("member_of")
            workspace_text = (
                str(workspace.object_value.entity_id)
                if workspace is not None and isinstance(workspace.object_value, EntityRef)
                else "unknown"
            )
            line = (
                f"- identity:{identity_id} role={role.object_value if role else 'unknown'} "
                f"workspace={workspace_text} HTTP {snapshot.http_status} "
                f"action={snapshot.action_id} evidence={snapshot.evidence_id}"
            )
            if len("\n".join((*lines, line))) > max_chars:
                break
            lines.append(line)
            selected.extend(fact.fact_id for fact in (role, workspace) if fact is not None)
        return lines, tuple(selected)

    def render_checked_actions(self, *, max_chars: int, max_entries: int = 16) -> list[str]:
        if not self._checked:
            return []
        lines = ["Previously checked requests (latest evidence for each exact fingerprint):"]
        for snapshot in reversed(tuple(self._checked.values())):
            identity = str(snapshot.identity_id) if snapshot.identity_id else "anonymous"
            response = snapshot.response_sha256[:16] if snapshot.response_sha256 else "unknown"
            line = (
                f"- {snapshot.method} {snapshot.path} as {identity} HTTP {snapshot.http_status} "
                f"action={snapshot.action_id} evidence={snapshot.evidence_id} "
                f"response_sha256_prefix={response}"
            )
            if len("\n".join((*lines, line))) > max_chars:
                break
            lines.append(line)
            if len(lines) - 1 >= max_entries:
                break
        return lines

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
