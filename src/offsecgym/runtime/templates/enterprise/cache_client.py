"""Tiny internal HTTP client for the synthetic authorization-cache service."""

import json
import os
from http.client import HTTPConnection
from urllib.parse import quote


class CacheClient:
    @staticmethod
    def _request(method, key, body=None):
        conn = HTTPConnection("cache", 8080, timeout=3)
        try:
            headers = {"X-Internal-Key": os.environ["OFFSECGYM_INSTANCE_SECRET"]}
            if body is not None:
                headers["Content-Type"] = "application/json"
            conn.request(
                method,
                "/internal/cache/" + quote(key, safe=""),
                body=json.dumps(body).encode() if body else None,
                headers=headers,
            )
            response = conn.getresponse()
            return response.status, json.loads(response.read(256))
        finally:
            conn.close()

    def get(self, key):
        status, body = self._request("GET", key)
        if status == 404:
            return None
        if status != 200:
            raise OSError("authorization cache unavailable")
        return body["value"].encode()

    def set(self, key, value):
        status, _ = self._request("POST", key, {"value": value})
        if status != 200:
            raise OSError("authorization cache unavailable")
