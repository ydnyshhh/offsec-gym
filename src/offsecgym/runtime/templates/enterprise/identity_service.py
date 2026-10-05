"""Synthetic identity and scoped membership service."""

import hashlib
import hmac
import re
from urllib.parse import urlsplit

from service_common import (
    Handler,
    audit,
    connect,
    listing,
    page,
    role_active,
    row,
    rows,
    serve,
    stamp,
    token_for,
)

PROJECT = re.compile(r"^/api/projects/([0-9a-f-]{36})$")
ROLES = re.compile(r"^/api/projects/([0-9a-f-]{36})/roles$")
REVOKE = re.compile(r"^/api/projects/([0-9a-f-]{36})/roles/([0-9a-f-]{36})/revoke$")
USER = re.compile(r"^/api/users/([0-9a-f-]{36})$")


class IdentityHandler(Handler):
    def handle_api(self):
        path = urlsplit(self.path).path
        if self.command == "POST" and path == "/api/login":
            return self.login()
        with connect() as conn:
            with conn.cursor() as cur:
                principal = self.principal(cur)
                if principal is None:
                    return self.error(401, "authentication required")
                user_id = principal["id"]
                if self.command == "GET" and path == "/api/me":
                    roles = rows(
                        cur,
                        """SELECT id,project_id,role,active,updated_at FROM role_assignments
                                        WHERE user_id=%s ORDER BY id""",
                        (user_id,),
                    )
                    return self.send_json(200, {**principal, "roles": roles})
                if self.command == "GET" and path in (
                    "/api/organizations",
                    "/api/projects",
                    "/api/users",
                ):
                    bounds = page(self)
                    if bounds is None:
                        return self.error(400, "invalid page")
                    if path == "/api/organizations":
                        data = rows(
                            cur,
                            "SELECT * FROM organizations WHERE id=%s ORDER BY id",
                            (principal["organization_id"],),
                        )
                    elif path == "/api/projects":
                        data = rows(
                            cur,
                            """SELECT * FROM projects WHERE organization_id=%s
                                           ORDER BY id""",
                            (principal["organization_id"],),
                        )
                    else:
                        data = rows(
                            cur,
                            """SELECT * FROM users WHERE organization_id=%s
                                           ORDER BY id""",
                            (principal["organization_id"],),
                        )
                    return self.send_json(200, listing(data, *bounds))
                if self.command == "GET" and (match := PROJECT.fullmatch(path)):
                    item = row(
                        cur,
                        """SELECT * FROM projects WHERE id=%s AND organization_id=%s""",
                        (match.group(1), principal["organization_id"]),
                    )
                    return self.send_json(200, item) if item else self.error(404, "not found")
                if self.command == "GET" and (match := USER.fullmatch(path)):
                    item = row(
                        cur,
                        """SELECT * FROM users WHERE id=%s AND organization_id=%s""",
                        (match.group(1), principal["organization_id"]),
                    )
                    return self.send_json(200, item) if item else self.error(404, "not found")
                if self.command == "GET" and (match := ROLES.fullmatch(path)):
                    project = row(
                        cur,
                        """SELECT * FROM projects WHERE id=%s AND organization_id=%s""",
                        (match.group(1), principal["organization_id"]),
                    )
                    if not project:
                        return self.error(404, "not found")
                    bounds = page(self)
                    if bounds is None:
                        return self.error(400, "invalid page")
                    data = rows(
                        cur,
                        """SELECT id,user_id,project_id,role,active,updated_at
                                       FROM role_assignments WHERE project_id=%s ORDER BY id""",
                        (project["id"],),
                    )
                    return self.send_json(200, listing(data, *bounds))
                if self.command == "POST" and (match := REVOKE.fullmatch(path)):
                    project_id, target_user_id = match.groups()
                    if not role_active(cur, user_id, project_id, "org_admin"):
                        return self.error(403, "operation not permitted")
                    body = self.body()
                    if body is None or body.get("role") not in (
                        "developer",
                        "operator",
                        "change_approver",
                        "member",
                        "auditor",
                    ):
                        return self.error(400, "invalid role request")
                    current = row(
                        cur,
                        """SELECT * FROM role_assignments WHERE project_id=%s
                                         AND user_id=%s AND role=%s AND active=true FOR UPDATE""",
                        (project_id, target_user_id, body["role"]),
                    )
                    if current is None:
                        return self.error(409, "assignment is not active")
                    cur.execute(
                        "UPDATE role_assignments SET active=false,updated_at=%s WHERE id=%s",
                        (stamp(cur), current["id"]),
                    )
                    audit(
                        cur,
                        user_id,
                        "role.revoked",
                        current["id"],
                        project_id,
                        "success",
                        executor="identity",
                    )
                    conn.commit()
                    return self.send_json(
                        200,
                        {
                            "id": current["id"],
                            "user_id": current["user_id"],
                            "project_id": current["project_id"],
                            "role": current["role"],
                            "active": False,
                        },
                    )
        return self.error(404, "not found")

    def login(self):
        body = self.body()
        if (
            body is None
            or not isinstance(body.get("username"), str)
            or not isinstance(body.get("password"), str)
        ):
            return self.error(400, "invalid credentials")
        with connect() as conn:
            with conn.cursor() as cur:
                user = row(
                    cur, "SELECT id,username FROM users WHERE username=%s", (body["username"],)
                )
        if user is None:
            return self.error(401, "invalid credentials")
        secret = __import__("os").environ["OFFSECGYM_INSTANCE_SECRET"]
        expected = hmac.new(secret.encode(), user["username"].encode(), hashlib.sha256).hexdigest()[
            :32
        ]
        if not hmac.compare_digest(expected, body["password"]):
            return self.error(401, "invalid credentials")
        return self.send_json(200, {"token": token_for(user["id"])})


if __name__ == "__main__":
    serve(IdentityHandler)
