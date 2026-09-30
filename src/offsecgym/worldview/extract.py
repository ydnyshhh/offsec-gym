"""Route-aware, schema-checked facts from trusted synthetic HTTP responses."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import EntityRef, FactValue

_WORKSPACE_LIST = re.compile(r"^/api/workspaces/([^/]+)/(documents|invoices|tickets)$")
_DETAIL = re.compile(r"^/api/(documents|invoices|support/tickets)/([^/]+)$")
_PREVIEW = re.compile(r"^/api/public/invoices/([^/]+)/preview$")
_REFUND = re.compile(r"^/api/invoices/([^/]+)/refund$")
_INVOICE_MENTION = re.compile(r"\binvoice ([0-9a-fA-F-]{36})\b")
_ENTITY_TYPES = {"documents": "document", "invoices": "invoice", "tickets": "ticket"}
MAX_BODY_EXCERPT = 512


@dataclass(frozen=True)
class ExtractedFact:
    kind: str
    subject: EntityRef
    predicate: str
    object_value: FactValue


def _uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _entity(entity_type: str, value: object) -> EntityRef | None:
    identifier = _uuid(value)
    return EntityRef(entity_type=entity_type, entity_id=identifier) if identifier else None


class ResponseFactExtractor:
    """Extract only fields verified in a complete 200 JSON response."""

    def extract(self, request: ActionRequest, result: ActionResult) -> tuple[ExtractedFact, ...]:
        if (
            request.destination != "saas"
            or result.status != "completed"
            or result.http_status != 200
            or result.truncated
            or result.evidence_id is None
            or result.body_text is None
            or result.response_sha256 is None
            or hashlib.sha256(result.body_text.encode("utf-8")).hexdigest()
            != result.response_sha256
        ):
            return ()
        try:
            body = json.loads(result.body_text)
        except (ValueError, TypeError):
            return ()
        if not isinstance(body, dict):
            return ()
        if request.method == "GET" and request.path == "/api/me":
            return self._identity(request, body)
        if request.method == "GET" and (match := _WORKSPACE_LIST.fullmatch(request.path)):
            return self._listing(match.group(1), match.group(2), body)
        if request.method == "GET" and (match := _DETAIL.fullmatch(request.path)):
            return self._detail(match.group(1), match.group(2), body)
        if request.method == "GET" and (match := _PREVIEW.fullmatch(request.path)):
            return self._preview(match.group(1), body)
        if request.method == "POST" and (match := _REFUND.fullmatch(request.path)):
            invoice = _entity("invoice", match.group(1))
            if (
                invoice
                and _uuid(body.get("id")) == invoice.entity_id
                and body.get("status") == "refunded"
            ):
                return (ExtractedFact("observation", invoice, "status", "refunded"),)
        return ()

    def _identity(
        self, request: ActionRequest, body: dict[str, object]
    ) -> tuple[ExtractedFact, ...]:
        identity = _entity("identity", body.get("id"))
        if identity is None or identity.entity_id != request.identity_id:
            return ()
        facts: list[ExtractedFact] = []
        role = body.get("role")
        if role in {"member", "workspace_admin", "support", "platform_admin"}:
            facts.append(ExtractedFact("observation", identity, "role", role))
        workspace = _entity("workspace", body.get("workspace_id"))
        if workspace:
            facts.append(ExtractedFact("relationship", identity, "member_of", workspace))
        username = body.get("username")
        if isinstance(username, str) and 0 < len(username) <= 256:
            facts.append(ExtractedFact("observation", identity, "username", username))
        return tuple(facts)

    def _listing(
        self, workspace_id: str, collection: str, body: dict[str, object]
    ) -> tuple[ExtractedFact, ...]:
        workspace = _entity("workspace", workspace_id)
        items = body.get("items")
        if workspace is None or not isinstance(items, list) or len(items) > 1000:
            return ()
        entity_type = _ENTITY_TYPES[collection]
        facts: list[ExtractedFact] = []
        for item in items:
            if not isinstance(item, dict) or _uuid(item.get("workspace_id")) != workspace.entity_id:
                continue
            entity = _entity(entity_type, item.get("id"))
            if entity is None:
                continue
            facts.extend(
                (
                    ExtractedFact("relationship", workspace, f"contains_{entity_type}", entity),
                    ExtractedFact("relationship", entity, "workspace", workspace),
                )
            )
        return tuple(facts)

    def _detail(
        self, collection: str, object_id: str, body: dict[str, object]
    ) -> tuple[ExtractedFact, ...]:
        entity = _entity(_ENTITY_TYPES[collection.rsplit("/", 1)[-1]], object_id)
        if entity is None or _uuid(body.get("id")) != entity.entity_id:
            return ()
        facts: list[ExtractedFact] = []
        workspace = _entity("workspace", body.get("workspace_id"))
        if workspace:
            facts.append(ExtractedFact("relationship", entity, "workspace", workspace))
        for field in ("status", "title", "billing_email", "amount_cents"):
            value = body.get(field)
            if field == "amount_cents" and type(value) is int and 0 <= value <= 10**18:
                facts.append(ExtractedFact("observation", entity, field, value))
            elif field != "amount_cents" and isinstance(value, str) and 0 < len(value) <= 2048:
                facts.append(ExtractedFact("observation", entity, field, value))
        if entity.entity_type in {"document", "ticket"}:
            detail_body = body.get("body")
            if isinstance(detail_body, str) and detail_body:
                facts.append(
                    ExtractedFact(
                        "observation",
                        entity,
                        "body_excerpt",
                        detail_body[:MAX_BODY_EXCERPT],
                    )
                )
                if len(detail_body) > MAX_BODY_EXCERPT:
                    facts.append(ExtractedFact("observation", entity, "body_truncated", True))
                match = _INVOICE_MENTION.search(detail_body)
                invoice = _entity("invoice", match.group(1)) if match else None
                if invoice:
                    facts.append(ExtractedFact("relationship", entity, "mentions_invoice", invoice))
        if entity.entity_type == "document":
            for field, target_type in (
                ("reference_document_id", "document"),
                ("reference_ticket_id", "ticket"),
            ):
                target = _entity(target_type, body.get(field))
                if target:
                    facts.append(ExtractedFact("relationship", entity, field, target))
        return tuple(facts)

    def _preview(self, object_id: str, body: dict[str, object]) -> tuple[ExtractedFact, ...]:
        invoice = _entity("invoice", object_id)
        if invoice is None or _uuid(body.get("id")) != invoice.entity_id:
            return ()
        facts: list[ExtractedFact] = []
        amount = body.get("amount_cents")
        if type(amount) is int and 0 <= amount <= 10**18:
            facts.append(ExtractedFact("observation", invoice, "public_amount_cents", amount))
        email = body.get("billing_email")
        if isinstance(email, str) and 0 < len(email) <= 2048:
            facts.append(ExtractedFact("observation", invoice, "public_billing_email", email))
        return tuple(facts)
