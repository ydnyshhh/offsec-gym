"""Access governance and change-control API backed by canonical PostgreSQL state."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from cache_client import CacheClient
from service_common import (
    Handler,
    audit,
    connect,
    listing,
    page,
    project_visible,
    role_active,
    row,
    rows,
    serve,
    stamp,
    tick,
)

PATCHES = frozenset(json.loads(Path("/app/implementation.json").read_text()))
ACCESS = re.compile(r"^/api/access-requests/([0-9a-f-]{36})(?:/(submit|approve|reject|revoke))?$")
CHANGE = re.compile(r"^/api/changes/([0-9a-f-]{36})(?:/(submit|approve|cancel|deploy))?$")
JOB = re.compile(r"^/api/jobs/([0-9a-f-]{36})$")
ENVIRONMENT = re.compile(r"^/api/environments/([0-9a-f-]{36})$")


def cache():
    return CacheClient()


def visible_item(cur, table, object_id, principal):
    return row(
        cur,
        f"""SELECT item.* FROM {table} item JOIN projects p
                        ON p.id=item.project_id WHERE item.id=%s AND p.organization_id=%s""",
        (object_id, principal["organization_id"]),
    )


def role_cached(cur, user_id, project_id):
    key = f"approval:{user_id}:{project_id}"
    cached = cache().get(key)
    if "B2-REVOKED-ROLE" not in PATCHES and cached is not None:
        return cached == b"1"
    current = role_active(cur, user_id, project_id, "change_approver")
    cache().set(key, "1" if current else "0")
    return current


class ChangeHandler(Handler):
    def handle_api(self):
        path = urlsplit(self.path).path
        with connect() as conn:
            with conn.cursor() as cur:
                principal = self.principal(cur)
                if principal is None:
                    return self.error(401, "authentication required")
                if self.command == "GET":
                    return self.get_resource(cur, principal, path)
                if self.command == "POST":
                    return self.mutate(conn, cur, principal, path)
        return self.error(404, "not found")

    def get_resource(self, cur, principal, path):
        user_id = principal["id"]
        bounds = page(self)
        if bounds is None:
            return self.error(400, "invalid page")
        if path in ("/api/access-requests", "/api/changes", "/api/jobs", "/api/environments"):
            table = {
                "/api/access-requests": "access_requests",
                "/api/changes": "changes",
                "/api/jobs": "jobs",
                "/api/environments": "environments",
            }[path]
            if table == "jobs":
                data = rows(
                    cur,
                    """SELECT j.* FROM jobs j JOIN changes c ON c.id=j.change_id
                                   JOIN projects p ON p.id=c.project_id
                                   WHERE p.organization_id=%s ORDER BY j.id""",
                    (principal["organization_id"],),
                )
            else:
                data = rows(
                    cur,
                    f"""SELECT item.* FROM {table} item JOIN projects p
                                    ON p.id=item.project_id WHERE p.organization_id=%s
                                    ORDER BY item.id""",
                    (principal["organization_id"],),
                )
            return self.send_json(200, listing(data, *bounds))
        if path == "/api/audit":
            data = rows(
                cur,
                """SELECT a.* FROM audit_events a JOIN projects p ON p.id=a.project_id
                               WHERE p.organization_id=%s ORDER BY a.sequence""",
                (principal["organization_id"],),
            )
            return self.send_json(200, listing(data, *bounds))
        if match := ACCESS.fullmatch(path):
            if match.group(2):
                return self.error(404, "not found")
            item = visible_item(cur, "access_requests", match.group(1), principal)
            return self.send_json(200, item) if item else self.error(404, "not found")
        if match := CHANGE.fullmatch(path):
            if match.group(2):
                return self.error(404, "not found")
            item = visible_item(cur, "changes", match.group(1), principal)
            if item:
                # Rendering available actions primes the same authorization
                # decision later consulted by the approval endpoint.
                can_approve = role_cached(cur, user_id, item["project_id"])
                allowed_actions = []
                if item["status"] == "PENDING_APPROVAL" and can_approve:
                    allowed_actions.append("reject")
                    if user_id != item["requester_id"]:
                        allowed_actions.append("approve")
                job = row(
                    cur,
                    "SELECT id,status FROM jobs WHERE change_id=%s "
                    "ORDER BY created_at DESC,id DESC LIMIT 1",
                    (item["id"],),
                )
                return self.send_json(
                    200,
                    {
                        **item,
                        "job_id": job["id"] if job else None,
                        "allowed_actions": allowed_actions,
                    },
                )
            return self.error(404, "not found")
        if match := JOB.fullmatch(path):
            item = row(
                cur,
                """SELECT j.* FROM jobs j JOIN changes c ON c.id=j.change_id
                              JOIN projects p ON p.id=c.project_id
                              WHERE j.id=%s AND p.organization_id=%s""",
                (match.group(1), principal["organization_id"]),
            )
            return self.send_json(200, item) if item else self.error(404, "not found")
        if match := ENVIRONMENT.fullmatch(path):
            item = visible_item(cur, "environments", match.group(1), principal)
            return self.send_json(200, item) if item else self.error(404, "not found")
        return self.error(404, "not found")

    def mutate(self, conn, cur, principal, path):
        actor = principal["id"]
        body = self.body()
        if body is None:
            return self.error(400, "invalid request body")
        if path == "/api/access-requests":
            project_id = body.get("project_id")
            if not isinstance(project_id, str) or not project_visible(cur, actor, project_id):
                return self.error(404, "not found")
            if body.get("requested_role") not in ("developer", "operator"):
                return self.error(400, "invalid role request")
            object_id = uuid5(UUID(str(actor)), f"access:{project_id}:{tick(cur)}")
            now = stamp(cur)
            cur.execute(
                """INSERT INTO access_requests(id,project_id,requester_id,target_user_id,
                           requested_role,status,revision,created_at,updated_at)
                           VALUES (%s,%s,%s,%s,%s,'DRAFT',1,%s,%s)""",
                (object_id, project_id, actor, actor, body["requested_role"], now, now),
            )
            audit(cur, actor, "access_request.created", object_id, project_id, "DRAFT")
            item = row(cur, "SELECT * FROM access_requests WHERE id=%s", (object_id,))
            conn.commit()
            return self.send_json(201, item)
        if path == "/api/changes":
            project_id = body.get("project_id")
            environment_id = body.get("environment_id")
            summary = body.get("summary")
            if not isinstance(project_id, str) or not project_visible(cur, actor, project_id):
                return self.error(404, "not found")
            environment = row(
                cur,
                "SELECT * FROM environments WHERE id=%s AND project_id=%s",
                (environment_id, project_id),
            )
            if environment is None or not isinstance(summary, str) or not 1 <= len(summary) <= 160:
                return self.error(400, "invalid change request")
            if not role_active(cur, actor, project_id, "developer"):
                return self.error(403, "operation not permitted")
            object_id = uuid5(UUID(str(actor)), f"change:{project_id}:{tick(cur)}")
            now = stamp(cur)
            cur.execute(
                """INSERT INTO changes(id,project_id,environment_id,requester_id,status,
                           revision,summary,created_at,updated_at)
                           VALUES (%s,%s,%s,%s,'DRAFT',1,%s,%s,%s)""",
                (object_id, project_id, environment_id, actor, summary, now, now),
            )
            audit(cur, actor, "change.created", object_id, project_id, "DRAFT")
            item = row(cur, "SELECT * FROM changes WHERE id=%s", (object_id,))
            conn.commit()
            return self.send_json(201, item)
        if match := ACCESS.fullmatch(path):
            object_id, operation = match.groups()
            if operation is None:
                return self.error(404, "not found")
            item = visible_item(cur, "access_requests", object_id, principal)
            if item is None:
                return self.error(404, "not found")
            cur.execute("SELECT id FROM access_requests WHERE id=%s FOR UPDATE", (object_id,))
            project_id = item["project_id"]
            if operation == "submit":
                if item["status"] != "DRAFT":
                    return self.error(409, "request cannot be modified in current state")
                if actor != item["requester_id"]:
                    return self.error(403, "operation not permitted")
                new_status = "PENDING"
            elif operation in ("approve", "reject"):
                if item["status"] != "PENDING":
                    return self.error(409, "request cannot be modified in current state")
                if not role_active(cur, actor, project_id, "change_approver"):
                    return self.error(403, "operation not permitted")
                if operation == "approve" and "B1-SOD" in PATCHES and actor == item["requester_id"]:
                    return self.error(403, "operation not permitted")
                new_status = "APPLIED" if operation == "approve" else "REJECTED"
            elif operation == "revoke":
                if item["status"] != "APPLIED":
                    return self.error(409, "request cannot be modified in current state")
                if actor != item["requester_id"] and not role_active(
                    cur, actor, project_id, "org_admin"
                ):
                    return self.error(403, "operation not permitted")
                new_status = "REVOKED"
            else:
                return self.error(404, "not found")
            cur.execute(
                """UPDATE access_requests SET status=%s,revision=revision+1,
                           approver_id=COALESCE(%s,approver_id),updated_at=%s WHERE id=%s""",
                (
                    new_status,
                    actor if operation in ("approve", "reject") else None,
                    stamp(cur),
                    object_id,
                ),
            )
            if operation == "approve":
                existing = row(
                    cur,
                    """SELECT id FROM role_assignments WHERE project_id=%s AND user_id=%s
                                      AND role=%s AND active=true""",
                    (project_id, item["target_user_id"], item["requested_role"]),
                )
                if existing is None:
                    grant_id = uuid5(UUID(str(object_id)), "applied-role")
                    cur.execute(
                        """INSERT INTO role_assignments(
                                   id,user_id,project_id,role,active,updated_at)
                                   VALUES (%s,%s,%s,%s,true,%s)""",
                        (
                            grant_id,
                            item["target_user_id"],
                            project_id,
                            item["requested_role"],
                            stamp(cur),
                        ),
                    )
            if operation == "revoke":
                cur.execute(
                    """UPDATE role_assignments SET active=false,updated_at=%s
                               WHERE project_id=%s AND user_id=%s AND role=%s AND active=true""",
                    (stamp(cur), project_id, item["target_user_id"], item["requested_role"]),
                )
            audit(cur, actor, f"access_request.{operation}", object_id, project_id, new_status)
            result = row(cur, "SELECT * FROM access_requests WHERE id=%s", (object_id,))
            conn.commit()
            if operation == "revoke":
                return self.send_empty()
            return self.send_json(200, result)
        if match := CHANGE.fullmatch(path):
            object_id, operation = match.groups()
            if operation is None:
                return self.error(404, "not found")
            item = visible_item(cur, "changes", object_id, principal)
            if item is None:
                return self.error(404, "not found")
            cur.execute("SELECT id FROM changes WHERE id=%s FOR UPDATE", (object_id,))
            project_id = item["project_id"]
            environment = row(
                cur, "SELECT * FROM environments WHERE id=%s", (item["environment_id"],)
            )
            if operation == "submit":
                if item["status"] not in ("DRAFT", "READY"):
                    return self.error(409, "request cannot be modified in current state")
                if actor != item["requester_id"]:
                    return self.error(403, "operation not permitted")
                new_status = (
                    "PENDING_APPROVAL" if environment["name"] == "production" else "APPROVED"
                )
            elif operation == "approve":
                if item["status"] != "PENDING_APPROVAL":
                    return self.error(409, "request cannot be modified in current state")
                if not role_cached(cur, actor, project_id):
                    return self.error(403, "operation not permitted")
                if actor == item["requester_id"] and environment["name"] == "production":
                    return self.error(403, "operation not permitted")
                new_status = "APPROVED"
            elif operation == "cancel":
                if item["status"] not in (
                    "DRAFT",
                    "READY",
                    "PENDING_APPROVAL",
                    "APPROVED",
                    "QUEUED",
                ):
                    return self.error(409, "request cannot be modified in current state")
                if actor != item["requester_id"] and not role_active(
                    cur, actor, project_id, "org_admin"
                ):
                    return self.error(403, "operation not permitted")
                new_status = "CANCELLED"
            elif operation == "deploy":
                if item["status"] != "APPROVED":
                    return self.error(409, "request cannot be modified in current state")
                if not role_active(cur, actor, project_id, "operator"):
                    return self.error(403, "operation not permitted")
                new_status = "QUEUED"
            else:
                return self.error(404, "not found")
            cur.execute(
                """UPDATE changes SET status=%s,revision=revision+1,
                           approver_id=COALESCE(%s,approver_id),updated_at=%s WHERE id=%s""",
                (new_status, actor if operation == "approve" else None, stamp(cur), object_id),
            )
            if operation == "deploy":
                job_id = uuid5(UUID(str(object_id)), f"job:{item['revision'] + 1}")
                now = stamp(cur)
                current_tick = tick(cur)
                cur.execute(
                    """INSERT INTO jobs(id,change_id,environment_id,initiator_id,status,
                               queued_tick,due_tick,queued_change_revision,queued_authorized,
                               created_at,updated_at)
                               VALUES (%s,%s,%s,%s,'QUEUED',%s,%s,%s,true,%s,%s)""",
                    (
                        job_id,
                        object_id,
                        item["environment_id"],
                        actor,
                        current_tick,
                        current_tick + 4,
                        item["revision"] + 1,
                        now,
                        now,
                    ),
                )
                audit(cur, actor, "job.queued", job_id, project_id, "QUEUED")
            audit(cur, actor, f"change.{operation}", object_id, project_id, new_status)
            result = row(cur, "SELECT * FROM changes WHERE id=%s", (object_id,))
            conn.commit()
            return self.send_json(202 if operation == "deploy" else 200, result)
        return self.error(404, "not found")


if __name__ == "__main__":
    serve(ChangeHandler)
