"""IPQualityScore phone validation. Needs ``IPQS_KEY``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="ipqualityscore",
    domain="ipqualityscore.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier", "fraud"),
    requires=("IPQS_KEY",),
    summary="validity, active line, carrier and risk signals",
)
async def ipqualityscore(ctx: CheckContext):
    key = ctx.credential("IPQS_KEY")
    response = await ctx.get(
        f"https://ipqualityscore.com/api/json/phone/{key}/{ctx.phone.e164}",
        params={"country[]": ctx.phone.iso or ""},
    )
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    if data.get("success") is False:
        message = str(data.get("message", "request rejected"))
        if "key" in message.lower() or "authori" in message.lower():
            return Status.NEEDS_CREDENTIALS, message[:120]
        if "quota" in message.lower() or "limit" in message.lower():
            return Status.RATE_LIMITED, message[:120]
        return Status.ERROR, message[:120]

    if data.get("valid") is False:
        return Status.NOT_FOUND, "not a valid number"
    bits = [
        data.get("carrier") or "",
        data.get("line_type") or "",
        f"active={data.get('active')}" if data.get("active") is not None else "",
        f"fraud={data.get('fraud_score')}" if data.get("fraud_score") is not None else "",
    ]
    return Status.FOUND, " · ".join(b for b in bits if b) or "valid number"
