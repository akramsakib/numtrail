"""A worked example of adding a site, using nothing but the public API.

Copy this file into ``numtrail/modules/`` (any sub-package will do) and it is
picked up automatically by discovery:

    $ cp examples/custom_module.py numtrail/modules/example.py
    $ numtrail --list-modules | grep example
    $ EXAMPLE_KEY=... numtrail +60123456789 -m example

Nothing here touches numtrail's internals: `ctx.phone`, `ctx.get/post` and the
return values are the whole contract.
"""

from __future__ import annotations

from numtrail import CheckContext, Status
from numtrail.registry import module


@module(
    name="example",
    domain="example.com",
    method="api",                 # api | register | login | other
    semantic="account_exists",    # or "number_valid" for validation APIs
    tier="api",
    tags=("api",),
    requires=("EXAMPLE_KEY",),    # reported as needs_credentials when unset
    summary="does example.com know this number",
)
async def example(ctx: CheckContext):
    # ctx.credential() raises MissingCredential when the env var is absent, which
    # the engine renders as [k] needs_credentials instead of an ugly traceback.
    key = ctx.credential("EXAMPLE_KEY")

    response = await ctx.get(
        "https://example.com/api/v1/lookup",
        params={"msisdn": ctx.phone.e164},   # +60123456789
        headers={"X-Api-Key": key, "Accept": "application/json"},
    )

    if response.status_code == 429:
        return Status.RATE_LIMITED, "quota finished"
    if response.status_code in (401, 403):
        return Status.NEEDS_CREDENTIALS, "key rejected"
    if response.status_code == 404:
        return Status.NOT_FOUND, "no account on this number"
    if response.status_code >= 500:
        return Status.ERROR, f"upstream {response.status_code}"

    try:
        data = response.json()
    except ValueError:
        # HTML where JSON was promised usually means a bot-wall, not a "no".
        return Status.BLOCKED, "non-JSON response"

    if not data.get("known"):
        return Status.NOT_FOUND, "no account on this number"
    return Status.FOUND, f"account {data.get('username', '')}".strip()
