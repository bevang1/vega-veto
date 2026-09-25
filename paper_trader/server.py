"""
A tiny local web server for the dashboard. Python standard library only.

  GET  /             the dashboard page
  GET  /api/state    everything the dashboard draws, as JSON (polled ~1x/sec)
  POST /api/control  {"action": "pause" | "resume" | "kill_all"}

It binds to 127.0.0.1, so only your own Mac can open it.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/state"):
                self._send(200, json.dumps(engine.state(), default=str).encode(), "application/json")
                return
            name = "index.html" if self.path in ("/", "") else self.path.lstrip("/").split("?")[0]
            file = (DASHBOARD / name).resolve()
            # Only serve files that really live inside dashboard/.
            if DASHBOARD in file.parents and file.is_file():
                self._send(200, file.read_bytes(), TYPES.get(file.suffix, "application/octet-stream"))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/control":
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                action = json.loads(self.rfile.read(length) or b"{}").get("action", "")
            except json.JSONDecodeError:
                action = ""
            msg = engine.control(action)
            self._send(200, json.dumps({"message": msg}).encode(), "application/json")

    return Handler


def serve(engine, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine))
