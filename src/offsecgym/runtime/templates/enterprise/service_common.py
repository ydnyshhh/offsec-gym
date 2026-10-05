"""Synthetic identity, JSON HTTP, and database helpers shared by range services."""

import base64
import hashlib
import hmac
import json
import os
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pg_client as psycopg

MAX_INPUT = 4096


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3)


def row(cursor, query, parameters=()):
    cursor.execute(query, parameters)
    return cursor.fetchone()


def rows(cursor, query, parameters=()):
    cursor.execute(query, parameters)
    return cursor.fetchall()


def scalar(cursor, query, parameters=()):
    result = row(cursor, query, parameters)
    return next(iter(result.values())) if result else None


def stamp(cursor):
    clock = row(cursor, "SELECT tick,origin FROM logical_clock WHERE singleton=true")
    return clock["origin"] + timedelta(seconds=clock["tick"])


def tick(cursor):
    return scalar(cursor, "SELECT tick FROM logical_clock WHERE singleton=true")


def audit(cursor, actor_id, action, object_id, project_id, status, executor="api"):
    cursor.execute(
        """INSERT INTO audit_events(
                      tick,actor_id,executor,action,object_id,project_id,status,created_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (tick(cursor), actor_id, executor, action, object_id, project_id, status, stamp(cursor)),
    )


def role_active(cursor, user_id, project_id, role):
    return bool(
        scalar(
            cursor,
            """SELECT EXISTS(SELECT 1 FROM role_assignments
                           WHERE user_id=%s AND project_id=%s AND role=%s AND active=true)""",
            (user_id, project_id, role),
        )
    )


def project_visible(cursor, user_id, project_id):
    return bool(
        scalar(
            cursor,
            """SELECT EXISTS(SELECT 1 FROM projects p JOIN users u
                          ON u.organization_id=p.organization_id WHERE u.id=%s AND p.id=%s)""",
            (user_id, project_id),
        )
    )


def token_for(user_id):
    value = str(user_id).encode()
    mac = hmac.new(os.environ["OFFSECGYM_INSTANCE_SECRET"].encode(), value, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(value + b"." + mac).decode().rstrip("=")


def token_user(header):
    if not header or not header.startswith("Bearer "):
        return None
    try:
        token = header[7:]
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        value, mac = raw.split(b".", 1)
        UUID(value.decode())
        expected = hmac.new(
            os.environ["OFFSECGYM_INSTANCE_SECRET"].encode(), value, hashlib.sha256
        ).digest()
        return value.decode() if hmac.compare_digest(mac, expected) else None
    except (ValueError, UnicodeError):
        return None


def json_value(value):
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    raise TypeError(type(value).__name__)


def page(handler):
    params = parse_qs(urlsplit(handler.path).query)
    try:
        limit = int(params.get("limit", ["10"])[0])
        offset = int(params.get("offset", ["0"])[0])
    except ValueError:
        return None
    return (min(limit, 20), offset) if 1 <= limit <= 20 and 0 <= offset <= 1000 else None


def listing(items, limit, offset):
    return {
        "items": items[offset : offset + limit],
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "total": len(items),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AlderControl/1.0"

    def log_message(self, *_args):
        return

    def send_json(self, code, body):
        raw = json.dumps(body, default=json_value, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header(
            "X-Request-Id",
            hashlib.sha256((self.command + self.path + str(code)).encode()).hexdigest()[:16],
        )
        self.end_headers()
        self.wfile.write(raw)

    def send_empty(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header(
            "X-Request-Id",
            hashlib.sha256((self.command + self.path + "204").encode()).hexdigest()[:16],
        )
        self.end_headers()

    def error(self, code, reason):
        self.send_json(code, {"error": reason})

    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= MAX_INPUT:
                return None
            raw = self.rfile.read(length)
            value = json.loads(raw) if raw else {}
            return value if isinstance(value, dict) else None
        except (ValueError, UnicodeError):
            return None

    def principal(self, cursor):
        user_id = token_user(self.headers.get("Authorization"))
        if user_id is None:
            return None
        return row(cursor, "SELECT * FROM users WHERE id=%s", (user_id,))

    def route(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
            return
        try:
            self.handle_api()
        except psycopg.Error:
            self.error(503, "service temporarily unavailable")

    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route()


def serve(handler, port=8080):
    HTTPServer(("0.0.0.0", port), handler).serve_forever()
