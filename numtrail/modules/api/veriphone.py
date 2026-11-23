"""Veriphone — free tier phone validation. Needs ``VERIPHONE_KEY``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="veriphone",
    domain="veriphone.io",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("VERIPHONE_KEY",),
    summary="free-tier validation: carrier, type, region",
)
async def veriphone(ctx: CheckContext):
    key = ctx.credential("VERIPHONE_KEY")
    response = await ctx.get(
        "https://api.veriphone.io/v2/verify",
        params={"phone": ctx.phone.e164, "key": key},
    )
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    if str(data.get("status", "")).lower() == "error":
        return Status.NEEDS_CREDENTIALS, str(data.get("message", "request rejected"))[:120]
    phone_valid = data.get("phone_valid")
    if phone_valid is False:
        return Status.NOT_FOUND, str(data.get("phone_valid") or "not a valid number")
    if phone_valid is None:
        return Status.ERROR, "unexpected payload"
    detail = " · ".join(str(p) for p in (
        data.get("carrier"), data.get("phone_type"), data.get("phone_region"),
    ) if p)
    return Status.FOUND, detail or "valid number"
