"""Sherlock and Maigret definitions, run for real against a fake transport.

The point of these tests is the same as the phone side: a check that got
stonewalled must say nothing, not "not found" and not "found". Sherlock and
Maigret both infer "exists" from the absence of an error signal, which today
reports every username as existing on client-rendered sites (Twitch, Pinterest,
x.com). The control-request comparison is what stops that.
"""

import asyncio

import httpx
import pytest

from numtrail.models import Status
from numtrail.usercheck import db, runner
from numtrail.usercheck.db import SiteDef

PROFILE = "<html><body><h1>Someone</h1><p>" + ("real profile content " * 40) + "</p></body></html>"
BLANK = "<html><body><h1>Not found</h1></body></html>"
SHELL = ("<html><body><div id=\"react-root\"></div><script src=\"/app.js\"></script>"
         "</body></html>")


def site(**kw) -> SiteDef:
    base = dict(name="Example", url="https://example.com/{}", domain="example.com",
                check="status_code")
    base.update(kw)
    return SiteDef(**base)


def run(s, handler, username="akram"):
    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                   follow_redirects=True, timeout=5)
        try:
            return await runner.check_site(client, s, username, timeout=5)
        finally:
            await client.aclose()

    return asyncio.run(scenario())


# ------------------------------------------------------------------ the database
def test_both_databases_are_vendored_with_licences():
    info = db.stats()
    assert info["sherlock"]["count"] == 478
    assert info["maigret"]["count"] == 3268
    for name in ("sherlock", "maigret"):
        assert info[name]["license"] == "MIT"
        assert info[name]["rev"], "record the upstream commit, so numbers are reproducible"
    assert info["maigret"]["total_in_upstream"] == 11071


def test_load_normalises_both_projects_onto_one_shape():
    for name in ("sherlock", "maigret"):
        sites = db.load(name)
        assert sites and all("{}" in s.url for s in sites)
        assert all(s.check in ("status_code", "message", "response_url") for s in sites)
        assert all(s.source == name for s in sites)
    assert any(s.name == "GitHub" for s in db.load("sherlock"))


def test_unknown_database_is_a_clear_error():
    with pytest.raises(KeyError):
        db.load("igornant")


def test_url_for_substitutes_the_username():
    assert site().url_for("akram") == "https://example.com/akram"


# ---------------------------------------------------------------- status_code
def test_status_200_with_a_404_control_is_found():
    def handler(request):
        if "n0trailcontrol" in str(request.url):
            return httpx.Response(404, text=BLANK)
        return httpx.Response(200, text=PROFILE)

    r = run(site(), handler)
    assert r.status is Status.FOUND
    assert "unused" in r.detail


def test_status_200_with_an_identical_control_is_not_found_but_unproven():
    """Pinterest answers 200 for any username; a bare 200 must not mean 'found'."""
    handler = lambda request: httpx.Response(200, text=PROFILE)  # noqa: E731
    r = run(site(), handler)
    assert r.status is Status.ERROR
    assert "same page" in r.detail


def test_status_404_is_not_found():
    r = run(site(), lambda request: httpx.Response(404, text=BLANK))
    assert r.status is Status.NOT_FOUND


def test_429_and_403_are_reported_as_such():
    assert run(site(), lambda r: httpx.Response(429)).status is Status.RATE_LIMITED
    assert run(site(), lambda r: httpx.Response(403)).status is Status.BLOCKED
    assert run(site(), lambda r: httpx.Response(500)).status is Status.ERROR


# --------------------------------------------------------------------- message
def test_error_string_means_absent():
    s = site(check="message", error_msg=("Sorry, nobody on Reddit goes by that name.",))
    r = run(s, lambda request: httpx.Response(200, text="<html>Sorry, nobody on Reddit goes by that name.</html>"))
    assert r.status is Status.NOT_FOUND
    assert "error string" in r.detail


def test_one_character_markers_are_ignored():
    """Twitch's shipped errorMsg is literally "c" — it matches every page."""
    s = site(check="message", error_msg=("c",))

    def handler(request):
        if "n0trailcontrol" in str(request.url):
            return httpx.Response(404, text=BLANK)
        return httpx.Response(200, text=PROFILE)

    assert run(s, handler).status is Status.FOUND


def test_maigret_presence_and_absence_markers():
    present = site(check="message", presence=('") | Codédex"',),
                   absence=("Profile not found",))
    assert run(present, lambda r: httpx.Response(
        200, text='<html>loc210z ") | Codédex"</html>')).status is Status.FOUND
    assert run(present, lambda r: httpx.Response(
        200, text="<html>Profile not found</html>")).status is Status.NOT_FOUND


def test_maigret_with_neither_marker_is_not_guessed():
    s = site(check="message", presence=("present-marker",), absence=("absent-marker",))
    r = run(s, lambda r: httpx.Response(200, text="<html>neither</html>"))
    assert r.status is Status.ERROR
    assert "neither" in r.detail


def test_app_shell_is_not_read_as_found():
    s = site(check="message", error_msg=("<title>429 Too Many Requests</title>",))
    r = run(s, lambda r: httpx.Response(200, text=SHELL))
    assert r.status is Status.ERROR
    assert "client-rendered" in r.detail


# ---------------------------------------------------------------- response_url
def test_response_url_that_redirects_away_is_not_found():
    def handler(request):
        if str(request.url).endswith("/akram"):
            return httpx.Response(302, headers={"location": "https://example.com/login"},
                                  text="")
        return httpx.Response(200, text="login")

    r = run(site(check="response_url"), handler)
    assert r.status is Status.NOT_FOUND
    assert "redirected" in r.detail


# ----------------------------------------------------------------------- misc
def test_regex_mismatch_is_skipped_without_a_request():
    def explode(request):  # pragma: no cover
        raise AssertionError("should not have been called")

    s = site(regex=r"^[a-z]+$")
    assert run(s, explode, username="Has_Underscore").status is Status.SKIPPED


def test_timeout_is_an_error_not_a_verdict():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    r = run(site(), handler)
    assert r.status is Status.ERROR
    assert "timeout" in r.detail


def test_post_sites_send_the_payload():
    bodies = []

    def handler(request):
        bodies.append(request.content.decode())
        if "n0trailcontrol" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, text=PROFILE)

    s = site(method="POST", payload='{"username":"{}"}')
    assert run(s, handler).status is Status.FOUND
    assert bodies[0] == '{"username":"akram"}'
    assert bodies[1] == '{"username":"n0trailcontrol9913"}'


def test_post_payloads_that_are_objects_do_not_explode():
    """Sherlock stores request_payload as a JSON string or a dict, per site."""
    bodies = []

    def handler(request):
        bodies.append(request.content.decode())
        if "n0trailcontrol" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, text=PROFILE)

    s = site(method="POST", payload={"username": "{}", "remember": True})
    assert run(s, handler).status is Status.FOUND
    assert bodies[0] == '{"username": "akram", "remember": true}'


def test_cli_writes_a_json_the_page_can_read(tmp_path, monkeypatch):
    """usernames.json is what the Pages site renders, so its shape is a contract."""
    import json

    async def fake_check_username(username, sites, *, limit=None, **kw):
        return [runner.UserResult("GitHub", "github.com", Status.FOUND, "HTTP 200",
                                  "https://github.com/" + username, 0.01, "sherlock"),
                runner.UserResult("Reddit", "reddit.com", Status.BLOCKED, "HTTP 403",
                                  "https://reddit.com/" + username, 0.01, "sherlock")]

    monkeypatch.setattr(runner, "check_username", fake_check_username)
    out = tmp_path / "usernames.json"
    from numtrail.usercheck.__main__ import main as cli

    assert cli(["akram", "--db", "sherlock", "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["generator"] == "numtrail.usercheck"
    assert data["latest"] == "akram"
    entry = data["usernames"]["akram"]
    assert set(entry) >= {"database", "generated_at", "checked", "conclusive", "results"}
    assert entry["checked"] == 2 and entry["generated_at"].endswith("Z")
    assert {r["status"] for r in entry["results"]} == {"found", "blocked"}


# ------------------------------------------------------------------- the store
def _run(name, *, when, status=Status.FOUND, database="sherlock", n=2):
    return {
        "username": name, "database": database, "generated_at": when,
        "checked": n, "conclusive": n,
        "results": [{"site": f"site{i}", "domain": f"site{i}.com",
                     "status": status.value, "found": status is Status.FOUND,
                     "url": f"https://site{i}.com/{name}", "detail": "x",
                     "elapsed": 0.1, "source": database} for i in range(n)],
    }


def test_store_keeps_several_usernames(tmp_path):
    from numtrail.usercheck import store

    path = tmp_path / "usernames.json"
    first = store.merge(store.load(path), "torvalds",
                        _run("torvalds", when="2026-01-01T00:00:00Z"))
    path.write_text(store.dumps(first))
    second = store.merge(store.load(path), "sakib112",
                         _run("sakib112", when="2026-01-02T00:00:00Z"))
    path.write_text(store.dumps(second))

    reloaded = store.load(path)
    assert set(store.runs(reloaded)) == {"torvalds", "sakib112"}
    assert reloaded["latest"] == "sakib112"
    assert store.get(reloaded, "@sakib112")["checked"] == 2


def test_store_migrates_the_old_single_run_file(tmp_path):
    import json

    from numtrail.usercheck import store

    path = tmp_path / "usernames.json"
    path.write_text(json.dumps(_run("torvalds", when="2026-01-01T00:00:00Z")))
    migrated = store.load(path)
    assert list(store.runs(migrated)) == ["torvalds"]
    assert migrated["latest"] == "torvalds"


def test_store_rerunning_a_username_replaces_it(tmp_path):
    from numtrail.usercheck import store

    path = tmp_path / "usernames.json"
    store_a = store.merge(store.load(path), "sakib112",
                          _run("sakib112", when="2026-01-01T00:00:00Z", n=2))
    store_b = store.merge(store_a, "sakib112",
                          _run("sakib112", when="2026-01-03T00:00:00Z", n=5))
    entry = store.get(store_b, "sakib112")
    assert entry["checked"] == 5
    assert entry["generated_at"] == "2026-01-03T00:00:00Z"
    assert len(store.runs(store_b)) == 1


def test_store_prunes_to_the_newest_and_stays_under_budget():
    from numtrail.usercheck import store

    s = store.empty()
    for day in range(1, 12):
        s = store.merge(s, f"user{day}",
                        _run(f"user{day}", when=f"2026-01-{day:02d}T00:00:00Z", n=40),
                        keep=3)
    assert len(store.runs(s)) == 3
    assert set(store.runs(s)) == {"user9", "user10", "user11"}

    s2 = store.merge(store.empty(), "big", _run("big", when="2026-02-01T00:00:00Z", n=4000),
                     keep=8, keep_bytes=5_000)
    assert store.runs(s2) == {} or len(store.runs(s2)) <= 1


def test_cli_merge_keeps_earlier_usernames(tmp_path, monkeypatch):
    from numtrail.usercheck.__main__ import main as cli

    async def fake_check_username(username, sites, *, limit=None, **kw):
        return [runner.UserResult("GitHub", "github.com", Status.FOUND, "HTTP 200",
                                  "https://github.com/" + username, 0.01, "sherlock")]

    monkeypatch.setattr(runner, "check_username", fake_check_username)
    out = tmp_path / "usernames.json"
    assert cli(["torvalds", "--json", str(out)]) == 0
    assert cli(["@sakib112", "--json", str(out)]) == 0

    import json
    data = json.loads(out.read_text())
    assert set(data["usernames"]) == {"torvalds", "sakib112"}
    assert data["latest"] == "sakib112"
