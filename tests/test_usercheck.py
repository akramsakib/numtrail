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
    assert info["maigret"]["count"] == 3278
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
