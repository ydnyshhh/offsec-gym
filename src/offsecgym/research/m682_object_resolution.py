"""Conservative, offline typing of verified public enterprise records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from offsecgym.runtime.enterprise import FAMILY

_ROUTES = {
    "/api/organizations": "organization",
    "/api/projects": "project",
    "/api/users": "identity",
    "/api/access-requests": "access_request",
    "/api/changes": "change_request",
    "/api/jobs": "job",
    "/api/environments": "environment",
    "/api/audit": "audit_event",
}
_DETAIL = {
    "projects": "project",
    "users": "identity",
    "access-requests": "access_request",
    "changes": "change_request",
    "jobs": "job",
    "environments": "environment",
}
# The historical model used the public route noun "change" as a shorthand.
# It remains parser-rejected; this map only measures the declared semantic type.
_DECLARED_ALIASES = {"change": "change_request", "user": "identity"}
_UUID_PATH = re.compile(r"^/api/([a-z-]+)/([0-9a-f-]{36})$")


@dataclass(frozen=True)
class PublicObject:
    identifier: str
    kind: str
    route: str
    json_location: str
    relationships: tuple[tuple[str, str], ...]


def canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def route_kind(method: str, path: str) -> str | None:
    """Map only documented GET record routes, ignoring query pagination."""
    if method != "GET":
        return None
    clean = path.split("?", 1)[0]
    if clean == "/api/me":
        return "identity"
    if clean in _ROUTES:
        return _ROUTES[clean]
    if clean.endswith("/roles") and re.fullmatch(r"/api/projects/[0-9a-f-]{36}/roles", clean):
        return "role_assignment"
    match = _UUID_PATH.fullmatch(clean)
    return _DETAIL.get(match.group(1)) if match else None


def _record(value: object, kind: str, route: str, location: str) -> PublicObject | None:
    if not isinstance(value, dict):
        return None
    identifier = canonical_uuid(value.get("id"))
    if identifier is None:
        return None
    links: list[tuple[str, str]] = []
    for field, target_kind in (
        ("organization_id", "organization"),
        ("project_id", "project"),
        ("user_id", "identity"),
        ("requester_id", "identity"),
        ("target_user_id", "identity"),
        ("approver_id", "identity"),
        ("environment_id", "environment"),
        ("change_id", "change_request"),
        ("job_id", "job"),
        ("initiator_id", "identity"),
        ("actor_id", "identity"),
    ):
        linked = canonical_uuid(value.get(field))
        if linked is not None:
            links.append((target_kind, linked))
    return PublicObject(identifier, kind, route, location, tuple(sorted(set(links))))


def objects_from_response(
    *, family: str, method: str, path: str, status: int, body: object, truncated: bool
) -> tuple[PublicObject, ...]:
    """Accept structural ID fields in complete, successful public GETs only."""
    if family != FAMILY or status != 200 or truncated:
        return ()
    kind = route_kind(method, path)
    if kind is None or not isinstance(body, dict):
        return ()
    route = path.split("?", 1)[0]
    if route in _ROUTES or route.endswith("/roles"):
        items = body.get("items")
        total = body.get("total")
        if not isinstance(items, list) or not isinstance(total, int) or total < len(items):
            return ()
        rows = [(item, f"$.items[{index}]") for index, item in enumerate(items)]
    else:
        rows = [(body, "$")]
    result = [
        item
        for value, location in rows
        if (item := _record(value, kind, route, location)) is not None
    ]
    if route == "/api/me" and isinstance(body.get("roles"), list):
        result.extend(
            item
            for index, value in enumerate(body["roles"])
            if (item := _record(value, "role_assignment", route, f"$.roles[{index}]")) is not None
        )
    return tuple(result)


def resolve(identifier: str | None, objects: tuple[PublicObject, ...]) -> tuple[str, ...]:
    """Return every independently established candidate kind, without guessing."""
    if identifier is None:
        return ()
    return tuple(sorted({item.kind for item in objects if item.identifier == identifier}))


def declared_kind_matches(declared: str | None, verified: tuple[str, ...]) -> bool | None:
    if declared is None or len(verified) != 1:
        return None
    return _DECLARED_ALIASES.get(declared, declared) == verified[0]
