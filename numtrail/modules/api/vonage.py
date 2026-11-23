"""Vonage (formerly Nexmo) Number Insight — Basic. Needs a key pair."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="vonage",
    domain="vonage.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("VONAGE_API_KEY", "VONAGE_API_SECRET"),
    summary="Number Insight Basic: parseable number, country, format",
)
async def vonage(ctx: CheckContext):
    api_key = ctx.credential("VONAGE_API_KEY")
    api_secret = ctx.credential("VONAGE_API_SECRET")

    response = await ctx.get(
        "https://api.nexmo.com/ni/basic/json",
        params={"api_key": api_key, "api_secret": api_secret, "number": ctx.phone.e164},
    )
    if response.status_code in (401, 403):
        return Status.NEEDS_CREDENTIALS, "vonage rejected the key pair"

    data = first_json(response)
    # Vonage reports outcome in the body, not only in the HTTP status.
    status_code = data.get("status")
    if status_code == 0:
        detail = " · ".join(str(p) for p in (
            data.get("country_code"), data.get("national_format"),
        ) if p)
        return Status.FOUND, detail or "valid number"
    if status_code == 3:      # invalid params
        return Status.NOT_FOUND, str(data.get("error_text") or "invalid number")
    if status_code == 4:      # invalid credentials
        return Status.NEEDS_CREDENTIALS, str(data.get("error_text") or "bad key pair")
    if status_code == 1:      # throttled
        return Status.RATE_LIMITED, str(data.get("error_text") or "throttled")

    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"
    return Status.ERROR, f"unexpected payload (status {status_code!r})"
