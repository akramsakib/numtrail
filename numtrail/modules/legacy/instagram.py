"""Instagram: attempt the account-creation lookup with a phone number.

Ported from megadose/ignorant (GPL-3.0), rebuilt around explicit statuses.
Instagram answers 429 for most datacentre IPs, so ``rate_limited`` is the
expected outcome unless a proxy is supplied.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json, looks_blocked

LOOKUP_URL = "https://i.instagram.com/api/v1/users/lookup/"
SIG_KEY_VERSION = "4"
SIG_KEY = "e6358aeede676184b9fe702b30f4fd35e71744605e39d2181a34cede076b3c33"


def sign(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"))
    digest = hmac.new(SIG_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()
    return ("ig_sig_key_version=" + SIG_KEY_VERSION
            + "&signed_body=" + digest + "." + urllib.parse.quote_plus(body))


def signed_payload(digits: str) -> dict:
    return {
        "login_attempt_count": "0",
        "directly_sign_in": "true",
        "source": "default",
        "q": digits,
        "ig_sig_key_version": SIG_KEY_VERSION,
    }


@module(
    name="instagram",
    domain="instagram.com",
    method="register",
    tier="legacy",
    tags=("social",),
    summary="account-creation lookup for a phone number",
)
async def instagram(ctx: CheckContext):
    response = await ctx.post(
        LOOKUP_URL,
        content=sign(signed_payload(ctx.phone.digits)),
        headers={
            "Accept-Language": "en-US",
            "User-Agent": "Instagram 219.0.0.12.117 Android",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-FB-HTTP-Engine": "Liger",
            "Connection": "close",
        },
    )

    transport = classify_transport(response)
    if transport is Status.RATE_LIMITED:
        return Status.RATE_LIMITED, "429 from the lookup endpoint; rotate IP with --proxy"
    if transport is not None:
        return transport, f"HTTP {response.status_code}"
    if looks_blocked(response.text):
        return Status.BLOCKED, "bot-wall served instead of JSON"

    data = first_json(response)
    message = str(data.get("message", ""))
    if message == "No users found":
        return Status.NOT_FOUND, "no account on this number"
    if data.get("users") or data.get("status") == "ok":
        return Status.FOUND, message or "account exists"
    if "checkpoint" in message or "challenge" in message.lower():
        return Status.BLOCKED, message
    return Status.ERROR, message or f"unexpected payload (HTTP {response.status_code})"
