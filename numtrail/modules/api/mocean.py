"""Mocean Number Lookup. Needs ``MOCEAN_API_KEY`` and ``MOCEAN_API_SECRET``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="mocean",
    domain="moceanapi.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("MOCEAN_API_KEY", "MOCEAN_API_SECRET"),
    summary="number lookup: country, carrier, reachability",
)
async def mocean(ctx: CheckContext):
    key = ctx.credential("MOCEAN_API_KEY")
    secret = ctx.credential("MOCEAN_API_SECRET")

    response = await ctx.get(
        "https://rest.moceanapi.com/rest/2/number/lookup",
        params={"mocean-api-key": key, "mocean-api-secret": secret,
                "mocean-to": ctx.phone.e164},
    )
    if response.status_code in (401, 403):
        return Status.NEEDS_CREDENTIALS, "mocean rejected the key pair"
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    if data.get("status") != 0:
        message = str(data.get("err_msg") or data.get("msg") or "request rejected")
        if "auth" in message.lower() or "key" in message.lower():
            return Status.NEEDS_CREDENTIALS, message[:120]
        return Status.NOT_FOUND, message[:120]
    bits = [data.get("country"), data.get("carrier"), data.get("number")]
    return Status.FOUND, " · ".join(str(b) for b in bits if b) or "valid number"
