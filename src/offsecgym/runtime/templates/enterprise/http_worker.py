"""Fixed controller dispatcher; no agent-selected host or direct worker route."""

import base64
import json
import os
import sys
from http.client import HTTPConnection
from urllib.parse import urlsplit

MAX_BODY = 16384
MAX_REQUEST = 4096


def valid_path(path):
    if not isinstance(path, str):
        return False
    try:
        parsed = urlsplit(path)
    except ValueError:
        return False
    return (
        path.startswith("/api/")
        and not path.startswith("//")
        and not parsed.scheme
        and not parsed.netloc
        and not parsed.fragment
        and not any(char in path for char in ("\r", "\n", "\x00", "\\"))
    )


def request(host, method, path, body=None, token=None, internal=False):
    conn = HTTPConnection(host, 8080, timeout=6)
    try:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        if internal:
            headers["X-Internal-Key"] = os.environ["OFFSECGYM_INSTANCE_SECRET"]
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response, response.read(MAX_BODY + 1)
    finally:
        conn.close()


def host_for(path):
    route = urlsplit(path).path
    if (
        route == "/api/me"
        or route.startswith("/api/organizations")
        or route.startswith("/api/projects")
        or route.startswith("/api/users")
    ):
        return "identity"
    if (
        route.startswith("/api/access-requests")
        or route.startswith("/api/changes")
        or route.startswith("/api/jobs")
        or route.startswith("/api/environments")
        or route.startswith("/api/audit")
    ):
        return "change"
    return "change"


def main():
    action = json.load(sys.stdin)
    method, path, body = action.get("method"), action.get("path"), action.get("json_body")
    identity = action.get("identity")
    if method not in ("GET", "POST") or not valid_path(path):
        raise ValueError("invalid range request")
    if body is not None and (method != "POST" or not isinstance(body, dict)):
        raise ValueError("invalid range body")
    encoded = (
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        if body is not None
        else None
    )
    if encoded is not None and len(encoded) > MAX_REQUEST:
        raise ValueError("body too large")
    token = None
    if identity is not None:
        if not isinstance(identity, dict) or set(identity) != {"username", "password"}:
            raise ValueError("invalid identity")
        login = json.dumps(identity, separators=(",", ":")).encode()
        login_response, login_body = request("identity", "POST", "/api/login", login)
        if login_response.status != 200:
            raise ValueError("identity login failed")
        token = json.loads(login_body)["token"]
    response, raw = request(host_for(path), method, path, encoded, token)
    settle, _ = request("worker", "POST", "/internal/settle", b"{}", internal=True)
    if settle.status != 200:
        raise ValueError("queue settlement failed")
    print(
        json.dumps(
            {
                "http_status": response.status,
                "body_b64": base64.b64encode(raw[:MAX_BODY]).decode(),
                "truncated": len(raw) > MAX_BODY,
                "content_type": response.getheader("Content-Type"),
                "redirect_location": response.getheader("Location"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from exc
