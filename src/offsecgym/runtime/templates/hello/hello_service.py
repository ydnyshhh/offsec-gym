"""Stateless, deterministic hello-range target."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect-out":
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/offsecgym")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path not in {"/health", "/hello"}:
            self.send_error(404)
            return
        payload = {"scenario": "health_check", "seed": int(os.environ["RANGE_SEED"])}
        if self.path == "/health":
            payload["status"] = "ok"
        else:
            payload["message"] = "hello from OffSecGym"
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
