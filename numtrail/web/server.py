"""Local web server: static UI + a streaming check API, stdlib only.

    python -m numtrail.web.server --port 8000
    # then open http://localhost:8000

The API is deliberately tiny so the same contract can be reimplemented anywhere
(see ``worker.js`` for the Cloudflare Workers build):

    POST /api/check   {"phone": "+60123456789"}
    -> application/x-ndjson, one JSON object per line:
       {"type":"meta",  "phone":"+60123456789", "iso":"MY", "automated":7, "total":153}
       {"type":"result","module":"instagram", "status":"found", "detail":"..."}
       {"type":"done",  "elapsed":1.42}

    GET  /api/catalog -> the site catalogue
    GET  /healthz     -> {"ok": true, ...}

Nothing about the number is written to disk or to a log. Per-IP rate limiting is
on by default; set ``NUMTRAIL_ALLOW_ORIGIN`` if you serve the UI from another
origin (e.g. GitHub Pages).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..catalog import SITES
from ..engine import Settings, run_checks
from ..phone import InvalidPhone, parse
from ..registry import discover, select

STATIC = Path(__file__).parent / "static"

RATE_LIMIT = int(os.environ.get("NUMTRAIL_RATE_LIMIT", "8"))       # runs per window
RATE_WINDOW = float(os.environ.get("NUMTRAIL_RATE_WINDOW", "60"))  # seconds
MAX_INFLIGHT = int(os.environ.get("NUMTRAIL_MAX_INFLIGHT", "4"))
ALLOW_ORIGIN = os.environ.get("NUMTRAIL_ALLOW_ORIGIN", "").strip()

_buckets: dict[str, list[float]] = {}
_bucket_lock = threading.Lock()
_inflight = threading.Semaphore(MAX_INFLIGHT)


def automated_modules() -> list[str]:
    """Module names the catalogue advertises, in catalogue order."""
    discover()
    known = {s.name for s in select()}
    return [site.probe for site in SITES if site.probe and site.probe in known]


def rate_limited(ip: str) -> int | None:
    """Return seconds to wait, or ``None`` when the caller may proceed."""
    now = time.time()
    with _bucket_lock:
        hits = [t for t in _buckets.get(ip, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE_LIMIT:
            return max(1, int(RATE_WINDOW - (now - hits[0])))
        hits.append(now)
        _buckets[ip] = hits
    return None


class Handler(SimpleHTTPRequestHandler):
    server_version = "numtrail"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    # ------------------------------------------------------------- plumbing
    def log_message(self, fmt: str, *args) -> None:  # noqa: D102 - quiet on purpose
        # Never log the request body or query: it can contain a phone number.
        if os.environ.get("NUMTRAIL_VERBOSE"):
            super().log_message(fmt, *args)

    def _cors(self) -> None:
        if ALLOW_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "content-type")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server naming
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------------------------------------------------------------ endpoints
    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] == "/healthz":
            modules = automated_modules()
            return self._json(200, {"ok": True, "sites": len(SITES),
                                    "automated": len(modules), "modules": modules})
        if self.path.split("?")[0] == "/api/catalog":
            body = (STATIC / "catalog.json").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/username":
            return self.username_check()
        if path != "/api/check":
            return self._json(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
            number = str(payload.get("phone", "")).strip()
        except (ValueError, AttributeError):
            return self._json(400, {"error": "body must be JSON like {\"phone\": \"+60...\"}"})

        if not number:
            return self._json(400, {"error": "send {\"phone\": \"+60123456789\"}"})

        client = self.client_address[0]
        wait = rate_limited(client)
        if wait is not None:
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", str(wait))
            self._cors()
            body = json.dumps({"error": f"rate limited, retry in {wait}s"}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            phone = parse(number)
        except InvalidPhone as exc:
            return self._json(400, {"error": str(exc)})

        modules = automated_modules()
        if not _inflight.acquire(blocking=False):
            return self._json(429, {"error": "busy, try again shortly"})

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors()
        self.end_headers()

        def write(event: dict) -> None:
            data = (json.dumps(event) + "\n").encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        started = time.perf_counter()
        try:
            write({"type": "meta", "phone": phone.e164, "iso": phone.iso,
                   "automated": len(modules), "total": len(SITES)})
            settings = Settings(timeout=20.0, retries=1, concurrency=6)
            asyncio.run(
                run_checks(
                    phone,
                    only=modules,
                    settings=settings,
                    on_result=lambda r: write({"type": "result", **r.as_dict()}),
                )
            )
            write({"type": "done", "elapsed": round(time.perf_counter() - started, 3)})
        except (BrokenPipeError, ConnectionResetError):
            pass  # client walked away; nothing to clean up
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            _inflight.release()


    # -------------------------------------------------------- username checks
    def username_check(self) -> None:
        """POST /api/username — run Sherlock/Maigret definitions server-side.

        The static deploy has no way to do this (Pages cannot make requests), so
        the page falls back to the committed usernames.json when this 404s.
        """
        from datetime import datetime, timezone

        from ..usercheck import db as userdb
        from ..usercheck import runner as userrunner

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"error": "body must be JSON"})

        username = str(payload.get("username", "")).strip().lstrip("@")
        database = str(payload.get("database", "sherlock"))
        try:
            limit = int(payload.get("limit") or 0) or None
        except (TypeError, ValueError):
            limit = None
        if not username:
            return self._json(400, {"error": "send {\"username\": \"akram\"}"})
        if database not in userdb.databases():
            return self._json(400, {"error": f"unknown database {database!r}"})

        wait = rate_limited(self.client_address[0])
        if wait is not None:
            return self._json(429, {"error": f"rate limited, retry in {wait}s"})
        if not _inflight.acquire(blocking=False):
            return self._json(429, {"error": "busy, try again shortly"})

        try:
            sites = userdb.load(database)
            results = asyncio.run(userrunner.check_username(
                username, sites, limit=limit))
        finally:
            _inflight.release()

        conclusive = sum(1 for r in results if r.status.conclusive)
        self._json(200, {
            "username": username,
            "database": database,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checked": len(results),
            "conclusive": conclusive,
            "results": [r.as_dict() for r in results],
        })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="numtrail-web",
                                     description="Serve the numtrail web UI locally")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    # Most PaaS hosts (Render, Fly, Railway, Cloud Run) inject $PORT and expect
    # you to bind it; local use wants 8000.
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--no-ratelimit", action="store_true",
                        help="disable the per-IP limit (local use only)")
    args = parser.parse_args(argv)

    if args.no_ratelimit:
        global RATE_LIMIT
        RATE_LIMIT = 10_000

    modules = automated_modules()
    print(f"numtrail web · {len(SITES)} sites · {len(modules)} automated probes: "
          f"{', '.join(modules)}")
    print(f"serving http://{args.host}:{args.port}  (Ctrl-C to stop)")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
