"""AbstractAPI phone validation. Needs ``ABSTRACTAPI_PHONE_KEY``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="abstractapi",
    domain="abstractapi.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("ABSTRACTAPI_PHONE_KEY",),
    summary="validity, format, carrier and location",
)
async def abstractapi(ctx: CheckContext):
    key = ctx.credential("ABSTRACTAPI_PHONE_KEY")
    response = await ctx.get(
        "https://phonevalidation.abstractapi.com/v1/",
        params={"api_key": key, "phone": ctx.phone.e164},
    )
    if response.status_code in (401, 422):
        return Status.NEEDS_CREDENTIALS, "abstractapi rejected the key"
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    if data.get("error"):
        return Status.ERROR, str(data["error"])[:120]
    if not data.get("valid"):
        return Status.NOT_FOUND, "not a valid number"
    carrier = data.get("carrier") or ""
    location = data.get("location") or ""
    detail = " · ".join(str(p) for p in (carrier, data.get("type"), location) if p)
    return Status.FOUND, detail or "valid number"
