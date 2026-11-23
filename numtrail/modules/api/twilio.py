"""Twilio Lookup v2 — authoritative validity, carrier and line type.

Needs ``TWILIO_ACCOUNT_SID`` and ``TWILIO_AUTH_TOKEN``.
"""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="twilio",
    domain="twilio.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"),
    summary="Lookup v2: valid, carrier, line type",
)
async def twilio(ctx: CheckContext):
    sid = ctx.credential("TWILIO_ACCOUNT_SID")
    token = ctx.credential("TWILIO_AUTH_TOKEN")

    response = await ctx.get(
        f"https://lookups.twilio.com/v2/PhoneNumbers/{ctx.phone.e164}",
        params={"Fields": "line_type_intelligence"},
        auth=(sid, token),
    )
    if response.status_code == 401:
        return Status.NEEDS_CREDENTIALS, "twilio rejected the SID/token pair"
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"
    if response.status_code == 404:
        return Status.NOT_FOUND, "not a valid number"

    data = first_json(response)
    if data.get("valid") is False:
        return Status.NOT_FOUND, data.get("validation_errors") or "invalid number"
    intel = (data.get("line_type_intelligence") or {})
    detail = " · ".join(
        str(part) for part in (
            intel.get("carrier_name") or "unknown carrier",
            intel.get("type") or "unknown type",
            data.get("country_code"),
        ) if part
    )
    return Status.FOUND, detail or "valid number"
