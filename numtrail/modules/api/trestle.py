"""Trestle Phone Intel. Needs ``TRESTLE_API_KEY``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="trestle",
    domain="trestleiq.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("TRESTLE_API_KEY",),
    summary="Phone Intel: validity, carrier, line type, prepaid flag",
)
async def trestle(ctx: CheckContext):
    key = ctx.credential("TRESTLE_API_KEY")
    response = await ctx.get(
        "https://api.trestleiq.com/3.0/phone_intel",
        params={"phone": ctx.phone.e164, "api_key": key},
    )
    if response.status_code in (401, 403):
        return Status.NEEDS_CREDENTIALS, "trestle rejected the key"
    if response.status_code == 404:
        return Status.NOT_FOUND, "not a valid number"
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    if data.get("is_valid") is False:
        return Status.NOT_FOUND, "not a valid number"
    bits = [data.get("carrier"), data.get("line_type"), data.get("country"),
            "prepaid" if data.get("is_prepaid") else ""]
    return Status.FOUND, " · ".join(str(b) for b in bits if b) or "valid number"
