"""Bird (formerly MessageBird) Lookup. Needs ``BIRD_ACCESS_KEY``."""

from __future__ import annotations

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, first_json


@module(
    name="bird",
    domain="bird.com",
    method="api",
    semantic="number_valid",
    tier="api",
    tags=("api", "carrier"),
    requires=("BIRD_ACCESS_KEY",),
    summary="Lookup HLR: country, carrier, line type",
)
async def bird(ctx: CheckContext):
    key = ctx.credential("BIRD_ACCESS_KEY")
    response = await ctx.get(
        f"https://lookup.messagebird.com/1/lookup/{ctx.phone.e164}",
        headers={"Authorization": f"AccessKey {key}"},
    )
    if response.status_code in (401, 403):
        return Status.NEEDS_CREDENTIALS, "bird rejected the access key"
    if response.status_code == 404:
        return Status.NOT_FOUND, "no route for this number"
    transport = classify_transport(response)
    if transport is not None:
        return transport, f"HTTP {response.status_code}"

    data = first_json(response)
    bits = [data.get("countryCode"), data.get("carrier"),
            data.get("type"), data.get("ported")]
    detail = " · ".join(str(b) for b in bits if b)
    return Status.FOUND, detail or "valid number"
