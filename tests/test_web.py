"""The local web server: contract tests against a stubbed engine, plus plumbing."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from numtrail.models import Result, Status
from numtrail.web import server as web


@pytest.fixture()
def live_server(monkeypatch):
    """A real HTTP server with the engine stubbed out (no network)."""

    async def fake_run_checks(phone, *, only, settings, on_result):
        for name in only:
            on_result(Result(module=name, domain=name + ".com", status=Status.FOUND,
                             detail="stub", tier="legacy"))
        return []

    monkeypatch.setattr(web, "run_checks", fake_run_checks)
    monkeypatch.setattr(web, "RATE_LIMIT", 100)

    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def post_check(base, payload):
    req = urllib.request.Request(base + "/api/check", method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        assert resp.headers["Content-Type"] == "application/x-ndjson"
        return [json.loads(line) for line in resp.read().decode().splitlines() if line.strip()]


def get_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


def test_healthz_advertises_the_probes(live_server):
    body = get_json(live_server + "/healthz")
    assert body["ok"] is True
    assert body["sites"] >= 100
    assert "instagram" in body["modules"]


def test_check_streams_meta_results_then_done(live_server):
    events = post_check(live_server, {"phone": "+60123456789"})
    assert events[0]["type"] == "meta"
    assert events[0]["iso"] == "MY"
    assert events[0]["total"] >= 100
    assert events[-1]["type"] == "done"
    middles = [e for e in events if e["type"] == "result"]
    assert middles and all(e["status"] == "found" for e in middles)


def test_bad_number_is_a_400(live_server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post_check(live_server, {"phone": "not-a-number"})
    assert excinfo.value.code == 400
    assert "dialling code" in json.loads(excinfo.value.read())["error"]


def test_missing_phone_is_a_400(live_server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post_check(live_server, {})
    assert excinfo.value.code == 400


def test_static_ui_is_served(live_server):
    with urllib.request.urlopen(live_server + "/", timeout=10) as resp:
        html = resp.read().decode()
    assert "numtrail" in html and "app.js" in html
    assert get_json(live_server + "/catalog.json")["sites"]


def test_unknown_post_route_404s(live_server):
    req = urllib.request.Request(live_server + "/api/nope", method="POST", data=b"{}")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(req, timeout=10)
    assert excinfo.value.code == 404


def test_rate_limiter_blocks_then_recovers(monkeypatch):
    monkeypatch.setattr(web, "RATE_LIMIT", 2)
    monkeypatch.setattr(web, "RATE_WINDOW", 0.4)
    web._buckets.clear()
    assert web.rate_limited("1.2.3.4") is None
    assert web.rate_limited("1.2.3.4") is None
    wait = web.rate_limited("1.2.3.4")
    assert isinstance(wait, int) and wait >= 1
    assert web.rate_limited("9.9.9.9") is None  # per client, not global
    import time

    time.sleep(0.45)
    assert web.rate_limited("1.2.3.4") is None


def test_automated_modules_are_derived_from_the_catalogue():
    modules = web.automated_modules()
    assert modules and "instagram" in modules
    assert len(modules) == len(set(modules))


# ------------------------------------------------------------------ usernames
def test_username_endpoint_runs_the_definitions(monkeypatch):
    from numtrail.models import Status as S
    from numtrail.usercheck import runner as userrunner
    from numtrail.usercheck.db import SiteDef

    async def fake_check_username(username, sites, *, limit=None, **kw):
        assert username == "akram"
        return [
            userrunner.UserResult("GitHub", "github.com", S.FOUND, "HTTP 200",
                                  "https://github.com/akram", 0.1, "sherlock"),
            userrunner.UserResult("Reddit", "reddit.com", S.BLOCKED, "HTTP 403",
                                  "https://reddit.com/akram", 0.1, "sherlock"),
        ]

    monkeypatch.setattr(userrunner, "check_username", fake_check_username)
    monkeypatch.setattr(web, "RATE_LIMIT", 100)

    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        req = urllib.request.Request(base + "/api/username", method="POST",
                                     data=json.dumps({"username": "@akram"}).encode(),
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert data["username"] == "akram"          # the @ is stripped
    assert data["checked"] == 2 and data["conclusive"] == 1
    assert [r["site"] for r in data["results"]] == ["GitHub", "Reddit"]
    assert data["results"][1]["status"] == "blocked"
    assert data["generated_at"].endswith("Z")


def test_username_endpoint_rejects_a_bad_request(live_server):
    def post(payload):
        req = urllib.request.Request(live_server + "/api/username", method="POST",
                                     data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            return 200, {}
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    assert post({"username": ""})[0] == 400
    assert post({"username": "akram", "database": "igornant"})[0] == 400
