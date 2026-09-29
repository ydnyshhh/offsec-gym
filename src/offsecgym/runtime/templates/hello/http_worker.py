"""One bounded HTTP action against the fixed hello service."""

import base64
import json
import sys
from http.client import HTTPConnection
from urllib.parse import urlsplit

MAX_BODY_BYTES = 16384


def main():
    request = json.load(sys.stdin)
    path = request.get("path")
    method = request.get("method")
    parsed = urlsplit(path) if isinstance(path, str) else None
    if (
        method != "GET"
        or parsed is None
        or not path.startswith("/")
        or path.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or any(character in path for character in ("\r", "\n", "\x00", "\\"))
    ):
        raise ValueError("invalid hello-range request")
    connection = HTTPConnection("hello", 8080, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        raw = response.read(MAX_BODY_BYTES + 1)
        payload = {
            "http_status": response.status,
            "body_b64": base64.b64encode(raw[:MAX_BODY_BYTES]).decode("ascii"),
            "truncated": len(raw) > MAX_BODY_BYTES,
            "content_type": response.getheader("Content-Type"),
            "redirect_location": response.getheader("Location"),
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from exc
