"""Snapchat: the signup phone validator.

Ported from megadose/ignorant (GPL-3.0). Snapchat moved the account pages to a
client-rendered app under ``/v2/``; the validator still exists but now answers
HTML to scripted clients unless a session cookie is supplied. Set
``NUMTRAIL_SNAPCHAT_COOKIE`` (a browser ``Cookie:`` header) to replay a real
session, otherwise expect ``blocked``.
"""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json

BASE = "https://accounts.snapchat.com"
VALIDATE_URL = f"{BASE}/accounts/validate_phone_number"

# Snapchat wants the ISO-3166 territory next to the number, not the dial code.
DIAL_TO_TERRITORY = {
    "1": "US", "20": "EG", "27": "ZA", "30": "GR", "31": "NL", "32": "BE",
    "33": "FR", "34": "ES", "36": "HU", "39": "IT", "40": "RO", "41": "CH",
    "43": "AT", "44": "GB", "45": "DK", "46": "SE", "47": "NO", "48": "PL",
    "49": "DE", "52": "MX", "55": "BR", "60": "MY", "61": "AU", "62": "ID",
    "63": "PH", "64": "NZ", "65": "SG", "66": "TH", "81": "JP", "82": "KR",
    "84": "VN", "86": "CN", "90": "TR", "91": "IN", "92": "PK", "971": "AE",
    "972": "IL", "966": "SA", "974": "QA", "880": "BD", "234": "NG",
    "254": "KE",
}


@module(
    name="snapchat",
    domain="snapchat.com",
    method="register",
    tier="legacy",
    tags=("social",),
    summary="phone validator from the signup flow (session cookie helps)",
)
async def snapchat(ctx: CheckContext):
    territory = DIAL_TO_TERRITORY.get(ctx.phone.dial) or ctx.phone.iso
    if not territory:
        return Status.SKIPPED, f"no territory mapping for +{ctx.phone.dial}"

    cookie = ctx.settings.credential("NUMTRAIL_SNAPCHAT_COOKIE") or ""
    headers = {
        "Origin": BASE,
        "Referer": f"{BASE}/",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    if cookie:
        headers["Cookie"] = cookie

    # A GET first, exactly like a browser, so the session cookies are set.
    prime = await ctx.get(f"{BASE}/", headers=headers)
    transport = classify_transport(prime)
    if transport is Status.BLOCKED:
        return Status.BLOCKED, f"HTTP {prime.status_code} on accounts.snapchat.com"
    if transport is not None and transport is not Status.ERROR:
        return transport, f"HTTP {prime.status_code} on accounts.snapchat.com"
    xsrf = prime.cookies.get("xsrf_token") or ""

    response = await ctx.post(
        VALIDATE_URL,
        headers=headers,
        data={
            "phone_country_code": territory,
            "phone_number": ctx.phone.national,
            "xsrf_token": xsrf,
        },
    )

    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    status_code = str(first_json(response).get("status_code", ""))
    if status_code == "TAKEN_NUMBER":
        return Status.FOUND, "number already attached to an account"
    if status_code == "OK":
        return Status.NOT_FOUND, "number still available at signup"
    if not status_code:
        return Status.BLOCKED, (
            "answered HTML instead of JSON: endpoint now needs a real session "
            "(set NUMTRAIL_SNAPCHAT_COOKIE)"
        )
    return Status.RATE_LIMITED if "RATE" in status_code else Status.ERROR, status_code
