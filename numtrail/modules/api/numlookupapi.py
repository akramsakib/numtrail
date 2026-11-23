"""numlookupapi.com — free tier available. Needs ``NUMLOOKUPAPI_KEY``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="numlookupapi",
    domain="numlookupapi.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("NUMLOOKUPAPI_KEY",),
    summary="validation with carrier, line type and country",
)
async def numlookupapi(ctx: CheckContext):
    key = ctx.credential("NUMLOOKUPAPI_KEY")
    response = await ctx.get(
        f"https://api.numlookupapi.com/v1/validate/{ctx.phone.e164}",
        params={"apikey": key},
    )
    if response.status_code in (401, 403):
        return Status.NEEDS_CREDENTIALS, "numlookupapi rejected the key"
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    if data.get("error"):
        message = str(data["error"].get("code") if isinstance(data["error"], dict)
                      else data["error"])
        if "key" in message.lower():
            return Status.NEEDS_CREDENTIALS, message
        return Status.ERROR, message[:120]

    if data.get("valid") is False:
        return Status.NOT_FOUND, "not a valid number"
    bits = [data.get("country_name"), data.get("carrier"), data.get("line_type")]
    return Status.FOUND, " · ".join(str(b) for b in bits if b) or "valid number"
