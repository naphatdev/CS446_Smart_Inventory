"""Local dev server: serves frontend/index.html and the same API the Lambda exposes.

    python backend/local_server.py            -> http://localhost:8000
Env: PORT, CHECK_EVERY (sec, default 300, 0=off), FX_EVERY (sec, default 3600, 0=off)
Data is stored in backend/data.json (delete file or press Reset demo data to start over).
"""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))

import lambda_api  # noqa: E402
import service  # noqa: E402

INDEX = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def _api(self):
        import json

        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        status, obj = lambda_api.handle(self.command, self.path.split("?")[0], raw)
        self._send(status, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(INDEX, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        self._api()

    do_POST = do_PUT = do_DELETE = _api

    def do_OPTIONS(self):
        self._send(204, b"")

    def log_message(self, fmt, *args):
        print(f"[http] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


def _loop(every, fn, name):
    while True:
        time.sleep(every)
        try:
            fn()
            print(f"[timer] {name} ran")
        except Exception as e:  # noqa: BLE001
            print(f"[timer] {name} failed: {e!r}")


if __name__ == "__main__":
    service.ensure_seeded()
    for env, default, fn, name in (
        ("CHECK_EVERY", 300, service.run_check, "checker"),
        ("FX_EVERY", 3600, service.refresh_rate, "fx"),
    ):
        every = int(os.environ.get(env, default))
        if every > 0:
            threading.Thread(target=_loop, args=(every, fn, name), daemon=True).start()
    port = int(os.environ.get("PORT", 8000))
    print(f"Smart Inventory on http://localhost:{port}")
    ThreadingHTTPServer(("", port), Handler).serve_forever()
