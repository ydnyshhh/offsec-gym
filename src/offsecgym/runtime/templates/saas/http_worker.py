"""One bounded, authenticated API request to the fixed SaaS service."""

import base64
import json
import sys
from http.client import HTTPConnection
from urllib.parse import urlsplit

MAX_BODY_BYTES = 16384
MAX_REQUEST_BYTES = 4096


def valid_path(path):
    if not isinstance(path, str):
        return False
    try:
        parsed = urlsplit(path)
    except ValueError:
        return False
    return (
        path.startswith("/")
        and not path.startswith("//")
        and not parsed.scheme
        and not parsed.netloc
        and not parsed.fragment
        and not any(character in path for character in ("\r", "\n", "\x00", "\\"))
    )


def request_once(connection, method, path, body=None, token=None):
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read(MAX_BODY_BYTES + 1)
    return response, raw


def main():
    request = json.load(sys.stdin)
    method = request.get("method")
    path = request.get("path")
    body = request.get("json_body")
    identity = request.get("identity")
    if method not in {"GET", "POST"} or not valid_path(path):
        raise ValueError("invalid SaaS request")
    if body is not None and (method != "POST" or not isinstance(body, dict)):
        raise ValueError("invalid SaaS body")
    encoded = (
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        if body is not None
        else None
    )
    if encoded is not None and len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError("SaaS body too large")
    token = None
    if identity is not None:
        if not isinstance(identity, dict) or set(identity) != {"username", "password"}:
            raise ValueError("invalid identity")
        login = json.dumps(identity, separators=(",", ":")).encode()
        connection = HTTPConnection("saas", 8080, timeout=5)
        try:
            response, raw = request_once(connection, "POST", "/api/login", login)
        finally:
            connection.close()
        if response.status != 200:
            raise ValueError("identity login failed")
        token = json.loads(raw)["token"]
    connection = HTTPConnection("saas", 8080, timeout=5)
    try:
        response, raw = request_once(connection, method, path, encoded, token)
        print(
            json.dumps(
                {
                    "http_status": response.status,
                    "body_b64": base64.b64encode(raw[:MAX_BODY_BYTES]).decode("ascii"),
                    "truncated": len(raw) > MAX_BODY_BYTES,
                    "content_type": response.getheader("Content-Type"),
                    "redirect_location": response.getheader("Location"),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from exc
