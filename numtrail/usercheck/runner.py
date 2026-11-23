"""Run Sherlock/Maigret definitions for real.

The three detection strategies those tools use, implemented the way they
implement them, with the same honesty rule as the phone side: a check that got
stonewalled says so instead of collapsing to "not found".

    status_code   200 -> found      404/410 -> not_found     403 -> blocked
                  429 -> rate_limited                       5xx -> error
    message       errorMsg / absenseStrs seen -> not_found
                  presence string seen        -> found
                  otherwise                   -> error (sherlock would say found)
    response_url  redirected away from the profile URL -> not_found
"""

from __future__ import annotations

import asyncio
import difflib
import re
import time
from dataclasses import dataclass

import httpx

from ..models import Status
from ..modules._util import looks_like_app_shell
from ..ua import browser_headers
from .db import SiteDef

__all__ = ["UserResult", "check_site", "check_username"]

# "The word 'a' is in every page" is not a signal. Sherlock and Maigret both ship
# a few one-character markers by accident (Twitch's errorMsg is literally "c"),
# which turn every username into "not found". Anything this short is ignored.
MIN_MARKER = 6
CONTROL_USERNAME = "n0trailcontrol9913"


@dataclass(slots=True)
class UserResult:
    site: str
    domain: str
    status: Status
    detail: str = ""
    url: str = ""
    elapsed: float = 0.0
    source: str = "sherlock"

    @property
    def found(self) -> bool:
        return self.status is Status.FOUND

    def as_dict(self) -> dict:
        return {
            "site": self.site,
            "domain": self.domain,
            "status": self.status.value,
            "found": self.found,
            "url": self.url,
            "detail": self.detail,
            "elapsed": round(self.elapsed, 3),
            "source": self.source,
        }


def _transport_status(code: int) -> Status | None:
    if code == 429:
        return Status.RATE_LIMITED
    if code in (401, 403):
        return Status.BLOCKED
    if code in (503, 521, 522, 524):
        return Status.BLOCKED
    if code >= 500:
        return Status.ERROR
    return None


def _usable(marker: str) -> bool:
    return bool(marker) and len(marker.strip()) >= MIN_MARKER


def _matches(regex: str | None, username: str) -> bool:
    if not regex:
        return True
    try:
        return re.fullmatch(regex, username) is not None
    except re.error:      # a broken pattern upstream must not break the run
        return True


async def check_site(client: httpx.AsyncClient, site: SiteDef, username: str,
                     *, timeout: float = 15.0) -> UserResult:
    url = site.url_for(username)
    started = time.perf_counter()

    if not _matches(site.regex, username):
        return UserResult(site.name, site.domain, Status.SKIPPED,
                          f"username does not match {site.regex}", url,
                          time.perf_counter() - started, site.source)

    try:
        if site.method == "POST":
            response = await client.post(url, content=site.body_for(username),
                                        headers={**browser_headers(), **(site.headers or {})},
                                        timeout=timeout)
        else:
            response = await client.get(url, headers={**browser_headers(), **(site.headers or {})},
                                        timeout=timeout)
    except httpx.TimeoutException:
        return UserResult(site.name, site.domain, Status.ERROR, "timeout", url,
                          time.perf_counter() - started, site.source)
    except httpx.HTTPError as exc:
        return UserResult(site.name, site.domain, Status.ERROR,
                          f"{type(exc).__name__}: {str(exc)[:80]}", url,
                          time.perf_counter() - started, site.source)
    except ValueError as exc:        # a template that is not a URL at all
        return UserResult(site.name, site.domain, Status.ERROR,
                          f"unusable definition: {str(exc)[:60]}", url,
                          time.perf_counter() - started, site.source)

    elapsed = time.perf_counter() - started
    code = response.status_code
    requested = url.rstrip("/")

    # response_url: the profile URL only serves a profile if it is still the URL
    if site.check == "response_url":
        if str(response.url).rstrip("/") == requested:
            return UserResult(site.name, site.domain, Status.FOUND,
                              "the profile URL answered without redirecting", url,
                              elapsed, site.source)
        return UserResult(site.name, site.domain, Status.NOT_FOUND,
                          f"redirected to {str(response.url)[:60]}", url,
                          elapsed, site.source)

    transport = _transport_status(code)
    if transport is not None:
        return UserResult(site.name, site.domain, transport, f"HTTP {code}", url,
                          elapsed, site.source)

    if site.check == "status_code":
        if code == 200:
            # Plenty of sites answer 200 with a "no such profile" page (Pinterest
            # and Twitch both do today), so a bare 200 is not proof. Compare
            # against an unused username before calling it found.
            verdict, why = await _control_check(client, site, username, code,
                                               response.text, timeout=timeout)
            if verdict is Status.ERROR:
                return UserResult(site.name, site.domain, verdict, why, url,
                                  elapsed, site.source)
            return UserResult(site.name, site.domain, Status.FOUND,
                              why or "HTTP 200", url, elapsed, site.source)
        if code in (404, 410):
            return UserResult(site.name, site.domain, Status.NOT_FOUND, f"HTTP {code}", url,
                              elapsed, site.source)
        if code in (301, 302):
            return UserResult(site.name, site.domain, Status.ERROR,
                              f"HTTP {code} not followed", url, elapsed, site.source)
        return UserResult(site.name, site.domain, Status.ERROR, f"HTTP {code}", url,
                          elapsed, site.source)

    # message
    body = response.text
    lowered = body.lower()
    for marker in site.absence:
        if _usable(marker) and marker.lower() in lowered:
            return UserResult(site.name, site.domain, Status.NOT_FOUND,
                              f"absent-marker {marker[:40]!r}", url, elapsed, site.source)
    for marker in site.error_msg:
        if _usable(marker) and marker.lower() in lowered:
            return UserResult(site.name, site.domain, Status.NOT_FOUND,
                              f"error string {marker[:40]!r} in the page", url,
                              elapsed, site.source)
    for marker in site.presence:
        if _usable(marker) and marker.lower() in lowered:
            return UserResult(site.name, site.domain, Status.FOUND,
                              f"presence-marker {marker[:40]!r}", url, elapsed, site.source)

    if site.presence:
        # Maigret's message checks name both sides; none of them appeared.
        return UserResult(site.name, site.domain, Status.ERROR,
                          "neither the presence nor the absence marker is on the page",
                          url, elapsed, site.source)
    if code != 200:
        return UserResult(site.name, site.domain, Status.ERROR, f"HTTP {code}", url,
                          elapsed, site.source)
    if looks_like_app_shell(body):
        return UserResult(site.name, site.domain, Status.ERROR,
                          "client-rendered shell: no error string either way, "
                          "so this cannot be verified (sherlock would say found)",
                          url, elapsed, site.source)

    # Last check before reporting "found": ask the same site for a username that
    # certainly does not exist. If the page comes back the same, the absence of an
    # error string means nothing — the site renders one page for everyone.
    verdict, why = await _control_check(client, site, username, code, body,
                                       timeout=timeout)
    if verdict is not None:
        return UserResult(site.name, site.domain, verdict, why, url, elapsed, site.source)

    return UserResult(site.name, site.domain, Status.FOUND,
                      "HTTP 200, none of the listed error strings, and a control "
                      "username looks different", url, elapsed, site.source)


def _visible(html: str) -> str:
    """Server-rendered text: scripts and styles out, whitespace collapsed."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return re.sub(r"\b[0-9a-f]{8,}\b", "#", text, flags=re.I)


async def _control_check(client: httpx.AsyncClient, site: SiteDef, username: str,
                         code: int, body: str, *,
                         timeout: float) -> tuple[Status | None, str]:
    """Ask the site for a username that certainly does not exist, and compare.

    "The page had no error string, so the account exists" is only worth
    something if a page *with* an error string looks different. Measured on
    Pinterest: a real profile is 499 characters of text, an unused one 185. On
    Twitch both are under 20 characters of script shell — there is nothing to
    compare, so the honest answer is "no signal", not "found".
    """
    if username == CONTROL_USERNAME:
        return None, ""
    control_url = site.url_for(CONTROL_USERNAME)
    control_headers = {**browser_headers(), **(site.headers or {})}
    try:
        if site.method == "POST":
            control = await client.post(control_url,
                                        content=site.body_for(CONTROL_USERNAME),
                                        headers=control_headers, timeout=timeout)
        else:
            control = await client.get(control_url, headers=control_headers,
                                       timeout=timeout)
    except httpx.HTTPError:
        return None, ""

    if control.status_code != code:
        return Status.FOUND, (f"HTTP {code} for this username but HTTP "
                              f"{control.status_code} for an unused one")

    mine, theirs = _visible(body), _visible(control.text)
    if len(mine) < 50:
        return Status.ERROR, (f"only {len(mine)} characters of server-rendered text — "
                              f"nothing to tell a profile from a blank page")
    # the username itself is the one thing that *must* differ, so blank it out
    mine = mine.lower().replace(username.lower(), "@")[:4000]
    theirs = theirs.lower().replace(CONTROL_USERNAME.lower(), "@")[:4000]
    ratio = difflib.SequenceMatcher(None, mine, theirs).quick_ratio()
    if ratio > 0.9:
        return Status.ERROR, ("an unused username returns the same page (similarity "
                              f"{ratio:.2f}) — the absence of an error string proves nothing")
    return None, ""


async def check_username(username: str, sites: list[SiteDef], *,
                         concurrency: int = 12, timeout: float = 15.0,
                         limit: int | None = None) -> list[UserResult]:
    chosen = sites[:limit] if limit else sites
    headers = browser_headers()
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(follow_redirects=True, headers=headers,
                                 timeout=timeout, limits=limits) as client:
        semaphore = asyncio.Semaphore(concurrency)

        async def one(site: SiteDef) -> UserResult:
            async with semaphore:
                return await check_site(client, site, username, timeout=timeout)

        return list(await asyncio.gather(*(one(s) for s in chosen)))
