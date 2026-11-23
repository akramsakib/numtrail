"""Instagram, ported exactly from megadose/ignorant (GPL-3.0).

ignorant's instagram.py, request for request:

    USERS_LOOKUP_URL = 'https://i.instagram.com/api/v1/users/lookup/'
    SIG_KEY_VERSION  = '4'
    IG_SIG_KEY       = 'e6358aee...076b3c33'          (public, shipped in ignorant)
    body  = 'ig_sig_key_version=4&signed_body=<hmac_sha256>.<urlquoted json>'
    json  = {login_attempt_count, directly_sign_in, source, q, ig_sig_key_version}
    q     = dial_code + subscriber digits, e.g. "60123456789"
    UA    = "Instagram 101.0.0.15.120"

ignorant reads the answer as: `message == "No users found"` → not used, and
*anything else* → used. The second half is wrong on today's API: a
`{"status":"fail","spam":true}` answer is a 429 body, not an account. So the
request is identical and the verdict is stricter:

    "No users found"             -> not_found
    users / status ok            -> found
    429, spam, checkpoint, HTML  -> rate_limited / blocked
    anything unparseable         -> error (never "not found")
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
# Shipped in ignorant as a constant; it signs the client-side lookup body.
IG_SIG_KEY = "e6358aeede676184b9fe702b30f4fd35e71744605e39d2181a34cede076b3c33"
IGNORANT_UA = "Instagram 101.0.0.15.120"


def generate_data(number_raw: str) -> dict:
    """ignorant.generate_data, unchanged."""
    return {
        "login_attempt_count": "0",
        "directly_sign_in": "true",
        "source": "default",
        "q": number_raw,
        "ig_sig_key_version": SIG_KEY_VERSION,
    }


def generate_signature(payload: dict) -> str:
    """ignorant.generate_signature, unchanged: body, not a dict."""
    body = json.dumps(payload)          # ignorant uses default separators
    digest = hmac.new(IG_SIG_KEY.encode("utf-8"), body.encode("utf-8"),
                      hashlib.sha256).hexdigest()
    return ("ig_sig_key_version=" + SIG_KEY_VERSION
            + "&signed_body=" + digest + "." + urllib.parse.quote_plus(body))


@module(
    name="instagram",
    domain="instagram.com",
    method="other",
    tier="legacy",
    tags=("social",),
    summary="signed users/lookup from the signup flow (ignorant protocol)",
)
async def instagram(ctx: CheckContext):
    body = generate_signature(generate_data(ctx.phone.dial + ctx.phone.national))
    response = await ctx.post(
        LOOKUP_URL,
        content=body,
        headers={
            "Accept-Language": "en-US",
            "User-Agent": IGNORANT_UA,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-FB-HTTP-Engine": "Liger",
            "Connection": "close",
        },
    )

    transport = classify_transport(response)
    if transport is Status.RATE_LIMITED:
        return Status.RATE_LIMITED, (
            f"HTTP 429 from the lookup endpoint (ignorant calls this 'rate limit'); "
            f"{response.text[:60]!r}")
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    if looks_blocked(response.text):
        return Status.BLOCKED, "bot-wall served instead of JSON"
    if "<html" in response.text[:400].lower() or "<!doctype" in response.text[:400].lower():
        return Status.BLOCKED, "the lookup answered with HTML, not JSON"

    data = first_json(response)
    message = str(data.get("message", ""))
    if message == "No users found":
        return Status.NOT_FOUND, "no users found for this number"
    if data.get("users"):
        return Status.FOUND, f"{len(data['users'])} account(s) on this number"
    if data.get("status") == "ok" and "user" in data:
        return Status.FOUND, message or "account exists"

    # Where ignorant reports "used", we say we don't know — and why.
    lowered = response.text.lower()
    if "checkpoint" in lowered or "challenge" in lowered:
        return Status.BLOCKED, "checkpoint / challenge instead of a lookup answer"
    if data.get("spam") or data.get("status") == "fail":
        return Status.RATE_LIMITED, (
            f"lookup refused the request ({response.text[:60]!r}); "
            f"ignorant would report 'used' here")
    return Status.ERROR, (
        f"no account signal in the payload (HTTP {response.status_code}); "
        f"ignorant would report 'used' here")
