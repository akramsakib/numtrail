"""Plivo Lookup. Needs ``PLIVO_AUTH_ID`` and ``PLIVO_AUTH_TOKEN``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="plivo",
    domain="plivo.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN"),
    summary="Lookup v1: carrier and country for a number",
)
async def plivo(ctx: CheckContext):
    auth_id = ctx.credential("PLIVO_AUTH_ID")
    token = ctx.credential("PLIVO_AUTH_TOKEN")

    response = await ctx.get(
        f"https://lookups.plivo.com/v1/Number/{ctx.phone.e164}/",
        params={"type": "carrier"},
        auth=(auth_id, token),
    )
    if response.status_code in (401, 403):
        return Status.NEEDS_CREDENTIALS, "plivo rejected the auth id/token pair"
    if response.status_code == 404:
        return Status.NOT_FOUND, "plivo does not recognise this number"
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    carrier = data.get("carrier") or {}
    bits = [
        carrier.get("name") if isinstance(carrier, dict) else carrier,
        data.get("country"),
        data.get("phone_number"),
    ]
    return Status.FOUND, " · ".join(str(b) for b in bits if b) or "valid number"
