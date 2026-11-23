"""Snapchat, ported exactly from megadose/ignorant (GPL-3.0).

ignorant's snapchat.py, request for request:

    GET  https://accounts.snapchat.com           (browser headers, to pick up cookies)
    POST https://accounts.snapchat.com/accounts/validate_phone_number
         phone_country_code = <ISO territory for the dial code, ignorant's own map>
         phone_number       = <subscriber digits, no dial code, no leading zero>
         xsrf_token         = <the xsrf_token cookie set by the GET>
    answer: {"status_code": "TAKEN_NUMBER"} -> used
            {"status_code": "OK"}           -> not used
            anything else                    -> rate limit (ignorant's fallback)

One deliberate difference: ignorant reads the cookie off the response object
(`response.cookies["xsrf_token"]`). Snapchat sets it on the redirect hop, so that
lookup raises KeyError and every run is reported as a rate limit. We read the
same cookie out of the session jar instead — same request, one fewer false
"rate limit".
"""

from __future__ import annotations

import random

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from ...ua import CHROME
from .._util import first_json, looks_blocked
from ..ignorant import DIAL_TO_TERRITORY

BASE = "https://accounts.snapchat.com"
VALIDATE_URL = f"{BASE}/accounts/validate_phone_number"

IGNORANT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en,en-US;q=0.5",
    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    "Origin": BASE,
    "DNT": "1",
    "Connection": "keep-alive",
    "Sec-GPC": "1",
    "TE": "Trailers",
}


def territory_for(dial: str, fallback: str | None = None) -> str | None:
    """ignorant's convert_to_country_code[country_code], without the KeyError."""
    return DIAL_TO_TERRITORY.get(dial) or fallback


@module(
    name="snapchat",
    domain="snapchat.com",
    method="register",
    tier="legacy",
    tags=("social",),
    summary="signup phone validator (ignorant protocol)",
)
async def snapchat(ctx: CheckContext):
    territory = territory_for(ctx.phone.dial, ctx.phone.iso)
    if not territory:
        return Status.SKIPPED, f"no territory in ignorant's map for +{ctx.phone.dial}"

    headers = {**IGNORANT_HEADERS, "User-Agent": random.choice(CHROME)}

    prime = await ctx.get(f"{BASE}/", headers=headers)
    if prime.status_code == 429:
        return Status.RATE_LIMITED, "HTTP 429 priming the signup page"
    if prime.status_code in (401, 403) or prime.status_code >= 500:
        return Status.BLOCKED, f"HTTP {prime.status_code} priming the signup page"

    xsrf = prime.cookies.get("xsrf_token") or ctx.client.cookies.get("xsrf_token") or ""
    if not xsrf:
        return Status.BLOCKED, (
            "the priming GET set no xsrf_token cookie, so the validator cannot be "
            "called the way ignorant calls it (typically an IP-level wall)")

    answer = await ctx.post(
        VALIDATE_URL,
        headers={**headers, "Referer": f"{BASE}/"},
        data={
            "phone_country_code": territory,
            "phone_number": ctx.phone.national,
            "xsrf_token": xsrf,
        },
    )

    if answer.status_code == 429:
        return Status.RATE_LIMITED, "HTTP 429 from validate_phone_number"
    if answer.status_code in (401, 403):
        return Status.BLOCKED, f"HTTP {answer.status_code} from validate_phone_number"

    if not answer.text.strip():
        return Status.BLOCKED, (
            "empty 200 body from validate_phone_number — the endpoint answered "
            "nothing (ignorant reports this as 'rate limit')")

    if looks_blocked(answer.text):
        return Status.BLOCKED, "bot-wall instead of a JSON verdict"
    if "<html" in answer.text[:400].lower() or "<!doctype" in answer.text[:400].lower():
        return Status.BLOCKED, "the validator answered with HTML, not a JSON verdict"

    payload = first_json(answer)
    code = payload.get("status_code")
    if code == "TAKEN_NUMBER":
        return Status.FOUND, f"TAKEN_NUMBER — an account holds this number ({territory})"
    if code == "OK":
        return Status.NOT_FOUND, f"OK — no account holds this number ({territory})"
    if code:
        return Status.RATE_LIMITED, f"status_code={code!r} (ignorant calls this 'rate limit')"
    return Status.ERROR, f"no status_code in the answer (HTTP {answer.status_code})"
