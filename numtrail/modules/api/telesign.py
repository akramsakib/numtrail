"""Telesign PhoneID. Needs ``TELESIGN_CUSTOMER_ID`` and ``TELESIGN_API_KEY``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="telesign",
    domain="telesign.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("TELESIGN_CUSTOMER_ID", "TELESIGN_API_KEY"),
    summary="PhoneID: number type, carrier, location",
)
async def telesign(ctx: CheckContext):
    customer = ctx.credential("TELESIGN_CUSTOMER_ID")
    api_key = ctx.credential("TELESIGN_API_KEY")

    response = await ctx.post(
        f"https://rest-ww.telesign.com/v1/phoneid/{ctx.phone.e164}",
        data={"account_lifecycle_event": "create"},
        auth=(customer, api_key),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code in (401, 403):
        return Status.NEEDS_CREDENTIALS, "telesign rejected the credentials"
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    status = data.get("status") or {}
    code = status.get("code")
    if code == 300:      # transaction successfully completed
        bits = [
            (data.get("phone_type") or {}).get("description"),
            (data.get("carrier") or {}).get("name"),
            (data.get("location") or {}).get("city"),
        ]
        return Status.FOUND, " · ".join(str(b) for b in bits if b) or "valid number"
    description = str(status.get("description", "unexpected status"))
    if code in (101, 102, 103, 104):   # bad/missing number variants
        return Status.NOT_FOUND, description
    return Status.RATE_LIMITED if code in (201, 202) else Status.ERROR, description
