"""Isolated, seeded AcmeCloud API with paired security predicates."""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

FIXTURE = json.loads(Path("/app/fixture.json").read_text(encoding="utf-8"))
SECRET = os.environ["OFFSECGYM_INSTANCE_SECRET"].encode()
PATCHED = os.environ["OFFSECGYM_PATCHED"] == "1"
DB = sqlite3.connect("/tmp/acmecloud.sqlite3")
DB.row_factory = sqlite3.Row
SESSIONS = {}


def password_for(username):
    return hmac.new(SECRET, username.encode(), hashlib.sha256).hexdigest()[:32]


def initialize():
    DB.executescript(
        """
        CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE accounts (
            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, role TEXT NOT NULL,
            workspace_id TEXT
        );
        CREATE TABLE documents (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
            body TEXT NOT NULL, reference_document_id TEXT, reference_ticket_id TEXT
        );
        CREATE TABLE invoices (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, amount_cents INTEGER NOT NULL,
            billing_email TEXT NOT NULL, status TEXT NOT NULL
        );
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
            body TEXT NOT NULL
        );
        CREATE TABLE replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL,
            author_id TEXT NOT NULL, body TEXT NOT NULL
        );
        """
    )
    DB.executemany("INSERT INTO workspaces VALUES (:id, :name)", FIXTURE["workspaces"])
    DB.executemany(
        "INSERT INTO accounts VALUES (:id, :username, :role, :workspace_id)",
        FIXTURE["accounts"],
    )
    DB.executemany(
        "INSERT INTO documents VALUES "
        "(:id, :workspace_id, :title, :body, :reference_document_id, :reference_ticket_id)",
        FIXTURE["documents"],
    )
    DB.executemany(
        "INSERT INTO invoices VALUES (:id, :workspace_id, :amount_cents, :billing_email, :status)",
        FIXTURE["invoices"],
    )
    DB.executemany(
        "INSERT INTO tickets VALUES (:id, :workspace_id, :title, :body)", FIXTURE["tickets"]
    )
    DB.commit()


def find(table, identifier):
    return DB.execute(f"SELECT * FROM {table} WHERE id = ?", (identifier,)).fetchone()


def visible(account, workspace_id, support_allowed=False):
    return (
        account["role"] == "platform_admin"
        or (support_allowed and account["role"] == "support")
        or account["workspace_id"] == workspace_id
    )


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, payload):
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 4096:
            raise ValueError("invalid body length")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("body must be an object")
        return payload

    def account(self):
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return None
        account_id = SESSIONS.get(authorization[7:])
        return find("accounts", account_id) if account_id else None

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            return self.send_json(200, {"status": "ok", "scenario": "tenant_boundary_v1"})
        if path == "/api/catalog":
            return self.send_json(
                200,
                {
                    "service": "AcmeCloud",
                    "routes": [
                        "/api/workspaces/{id}/documents",
                        "/api/workspaces/{id}/invoices",
                        "/api/workspaces/{id}/tickets",
                        "/api/documents/{id}",
                        "/api/invoices/{id}",
                        "/api/support/tickets/{id}",
                        "/api/public/invoices/{id}/preview",
                    ],
                },
            )
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[:3] == ["api", "public", "invoices"] and parts[4] == "preview":
            invoice = find("invoices", parts[3])
            if invoice is None:
                return self.send_json(404, {"error": "not_found"})
            preview = {"id": invoice["id"], "amount_cents": invoice["amount_cents"]}
            if not PATCHED:
                preview["billing_email"] = invoice["billing_email"]
            return self.send_json(200, preview)
        account = self.account()
        if account is None:
            return self.send_json(401, {"error": "authentication_required"})
        if path == "/api/me":
            return self.send_json(200, dict(account))
        if len(parts) == 4 and parts[:2] == ["api", "workspaces"]:
            workspace_id, collection = parts[2], parts[3]
            if find("workspaces", workspace_id) is None:
                return self.send_json(404, {"error": "not_found"})
            if collection == "members":
                if account["role"] != "platform_admin" and not (
                    account["role"] == "workspace_admin" and account["workspace_id"] == workspace_id
                ):
                    return self.send_json(403, {"error": "forbidden"})
                rows = DB.execute(
                    "SELECT id, username, role, workspace_id FROM accounts WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchall()
                return self.send_json(200, {"items": [dict(row) for row in rows]})
            if collection in {"documents", "invoices", "tickets"}:
                if not visible(account, workspace_id, support_allowed=collection == "tickets"):
                    return self.send_json(403, {"error": "forbidden"})
                rows = DB.execute(
                    f"SELECT id, workspace_id FROM {collection} WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchall()
                return self.send_json(200, {"items": [dict(row) for row in rows]})
        if len(parts) == 3 and parts[:2] == ["api", "documents"]:
            document = find("documents", parts[2])
            if document is None:
                return self.send_json(404, {"error": "not_found"})
            if PATCHED and not visible(account, document["workspace_id"]):
                return self.send_json(403, {"error": "forbidden"})
            return self.send_json(200, dict(document))
        if len(parts) == 3 and parts[:2] == ["api", "invoices"]:
            invoice = find("invoices", parts[2])
            if invoice is None:
                return self.send_json(404, {"error": "not_found"})
            if PATCHED and not visible(account, invoice["workspace_id"]):
                return self.send_json(403, {"error": "forbidden"})
            return self.send_json(200, dict(invoice))
        if len(parts) == 4 and parts[:3] == ["api", "support", "tickets"]:
            ticket = find("tickets", parts[3])
            if ticket is None:
                return self.send_json(404, {"error": "not_found"})
            if PATCHED and not visible(account, ticket["workspace_id"], support_allowed=True):
                return self.send_json(403, {"error": "forbidden"})
            return self.send_json(200, dict(ticket))
        return self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/login":
            try:
                body = self.body_json()
            except (ValueError, json.JSONDecodeError):
                return self.send_json(400, {"error": "invalid_request"})
            username, password = body.get("username"), body.get("password")
            account = DB.execute(
                "SELECT * FROM accounts WHERE username = ?", (username,)
            ).fetchone()
            if (
                account is None
                or not isinstance(password, str)
                or not hmac.compare_digest(password, password_for(username))
            ):
                return self.send_json(401, {"error": "invalid_credentials"})
            if len(SESSIONS) >= 1024:
                SESSIONS.pop(next(iter(SESSIONS)))
            token = secrets.token_urlsafe(24)
            SESSIONS[token] = account["id"]
            return self.send_json(200, {"token": token, "identity_id": account["id"]})
        account = self.account()
        if account is None:
            return self.send_json(401, {"error": "authentication_required"})
        try:
            body = self.body_json()
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "invalid_request"})
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "invoices"] and parts[3] == "refund":
            invoice = find("invoices", parts[2])
            if invoice is None:
                return self.send_json(404, {"error": "not_found"})
            authorized = account["role"] == "platform_admin" or (
                account["workspace_id"] == invoice["workspace_id"]
                and (not PATCHED or account["role"] == "workspace_admin")
            )
            if not authorized:
                return self.send_json(403, {"error": "forbidden"})
            if invoice["status"] != "paid":
                return self.send_json(409, {"error": "already_refunded"})
            if not isinstance(body.get("reason"), str) or not body["reason"].strip():
                return self.send_json(400, {"error": "reason_required"})
            DB.execute("UPDATE invoices SET status = 'refunded' WHERE id = ?", (invoice["id"],))
            DB.commit()
            return self.send_json(200, {"id": invoice["id"], "status": "refunded"})
        if len(parts) == 5 and parts[:3] == ["api", "support", "tickets"] and parts[4] == "reply":
            ticket = find("tickets", parts[3])
            if ticket is None:
                return self.send_json(404, {"error": "not_found"})
            if account["role"] not in {"support", "platform_admin"}:
                return self.send_json(403, {"error": "forbidden"})
            if not isinstance(body.get("body"), str) or not body["body"].strip():
                return self.send_json(400, {"error": "body_required"})
            DB.execute(
                "INSERT INTO replies (ticket_id, author_id, body) VALUES (?, ?, ?)",
                (ticket["id"], account["id"], body["body"][:512]),
            )
            DB.commit()
            return self.send_json(201, {"ticket_id": ticket["id"], "status": "replied"})
        return self.send_json(404, {"error": "not_found"})

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    initialize()
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
