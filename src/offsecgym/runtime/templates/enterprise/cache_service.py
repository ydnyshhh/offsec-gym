"""Isolated synthetic authorization cache with deterministic no-expiry snapshots."""

import hmac
import os
from urllib.parse import unquote, urlsplit

from service_common import Handler, serve

CACHE = {}


class CacheHandler(Handler):
    def handle_api(self):
        if not hmac.compare_digest(
            self.headers.get("X-Internal-Key", ""), os.environ["OFFSECGYM_INSTANCE_SECRET"]
        ):
            return self.error(403, "operation not permitted")
        path = urlsplit(self.path).path
        if not path.startswith("/internal/cache/"):
            return self.error(404, "not found")
        key = unquote(path.removeprefix("/internal/cache/"))
        if not key or len(key) > 180:
            return self.error(400, "invalid key")
        if self.command == "GET":
            value = CACHE.get(key)
            return self.send_json(
                200 if value is not None else 404,
                {"value": value} if value is not None else {"error": "not found"},
            )
        if self.command == "POST":
            body = self.body()
            if body is None or body.get("value") not in ("0", "1"):
                return self.error(400, "invalid value")
            CACHE[key] = body["value"]
            return self.send_json(200, {"stored": True})
        return self.error(404, "not found")


if __name__ == "__main__":
    serve(CacheHandler)
