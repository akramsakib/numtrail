"""Numverify (apilayer) — validity, carrier and location. Needs ``NUMVERIFY_KEY``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="numverify",
    domain="numverify.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("NUMVERIFY_KEY",),
    summary="number validation with carrier metadata",
)
async def numverify(ctx: CheckContext):
    key = ctx.credential("NUMVERIFY_KEY")
    response = await ctx.get(
        "https://apilayer.net/api/validate",
        params={"access_key": key, "number": ctx.phone.digits,
                "country_code": ctx.phone.iso or "", "format": "1"},
    )
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    error = data.get("error") or {}
    if error:
        code = error.get("code")
        if code in (101, 102, 103):
            return Status.NEEDS_CREDENTIALS, f"{error.get('type', 'invalid key')}"
        if code in (104, 105):
            return Status.RATE_LIMITED, f"{error.get('type', 'quota exceeded')}"
        return Status.ERROR, str(error.get("info") or error.get("type") or code)

    if not data.get("valid"):
        return Status.NOT_FOUND, "not a valid number"
    detail = " · ".join(str(p) for p in (data.get("carrier"), data.get("line_type"),
                                        data.get("location")) if p)
    return Status.FOUND, detail or "valid number"
