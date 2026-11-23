"""Amazon, ported exactly from megadose/ignorant (GPL-3.0).

ignorant's amazon.py, request for request:

    GET  https://www.amazon.com/ap/signin?openid...&          (ignorant's exact URL,
         with a random desktop Chrome UA; no cookie jar priming)
    every <form input> with a name and a value becomes POST data
    data["email"] = dial_code + subscriber digits
    POST https://www.amazon.com/ap/signin/
    <div id="auth-password-missing-alert"> in the answer  -> used
    otherwise                                             -> not used

That last line is where ignorant lies. Amazon answers a scripted claim POST with
its "continue shopping" captcha page, which contains no password alert — so the
probe reports "this number has no Amazon account" for every number on earth,
including ones that do. Measured here on 2026-11-23: the same 3848-byte captcha
page comes back for a real-looking number and for
`notarealaddress_zzz9911@example.com`.

So: identical requests, but `not used` is only reported when Amazon actually
says so ("We cannot find an account with that email address"). A captcha, a
generic page, or a redirect to /ax/claim reports blocked/error instead.
"""

from __future__ import annotations

import random

from bs4 import BeautifulSoup

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from ...ua import CHROME
from .._util import looks_blocked

BASE = "https://www.amazon.com"

# Byte for byte the URL in ignorant/modules/shopping/amazon.py.
SIGNIN_URL = (
    BASE + "/ap/signin?openid.pape.max_auth_age=0"
    "&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F%3F_encoding%3DUTF8"
    "%26ref_%3Dnav_ya_signin"
    "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
    "&openid.assoc_handle=usflex&openid.mode=checkid_setup"
    "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
    "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0&"
)
POST_URL = BASE + "/ap/signin/"

# What actually means "there is an account": Amazon offers the password step.
HAS_ACCOUNT_MARKERS = (
    'id="auth-password-missing-alert"',      # ignorant's marker, unchanged
    'name="ap_password"',
    "auth-password-missing-alert",
)
# What actually means "there is no account".
NO_ACCOUNT_MARKERS = (
    "we cannot find an account with that email address",
    "cannot find an account with that",
    "no account found with that",
    "we could not find an account",
)
CAPTCHA_MARKERS = ("validatecaptcha", "/errors_page/", "click the button below to continue")


def form_inputs(html: str) -> dict[str, str]:
    """ignorant's dict comprehension: every input that has both name and value."""
    soup = BeautifulSoup(html, "html.parser")
    return {x["name"]: x["value"] for x in soup.select("form input")
            if "name" in x.attrs and "value" in x.attrs}


@module(
    name="amazon",
    domain="amazon.com",
    method="login",
    tier="legacy",
    tags=("shopping",),
    summary="sign-in claim flow with the number as the identifier (ignorant protocol)",
)
async def amazon(ctx: CheckContext):
    headers = {"User-Agent": random.choice(CHROME), "Accept-Language": "en-US,en;q=0.9"}

    page = await ctx.get(SIGNIN_URL, headers=headers)
    if page.status_code == 429:
        return Status.RATE_LIMITED, "HTTP 429 on the sign-in page"
    if page.status_code in (401, 403) or page.status_code >= 500:
        return Status.BLOCKED, f"HTTP {page.status_code} on the sign-in page"

    fields = form_inputs(page.text)
    if not fields:
        return Status.BLOCKED, "the sign-in page carried no form inputs"

    fields["email"] = ctx.phone.digits        # ignorant: str(country_code)+str(phone)
    claim = await ctx.post(POST_URL, data=fields,
                           headers={**headers, "Referer": SIGNIN_URL})

    if claim.status_code == 406:
        return Status.BLOCKED, "claim POST rejected (HTTP 406) — this is a robot to Amazon"
    if claim.status_code == 429:
        return Status.RATE_LIMITED, "HTTP 429 on the claim POST"
    if claim.status_code >= 500:
        return Status.ERROR, f"HTTP {claim.status_code} on the claim POST"

    body = claim.text
    lowered = body.lower()

    if any(marker in lowered for marker in NO_ACCOUNT_MARKERS):
        return Status.NOT_FOUND, "Amazon: 'we cannot find an account with that email address'"
    if any(marker in lowered for marker in HAS_ACCOUNT_MARKERS):
        return Status.FOUND, "password step offered for this number"
    if any(marker in lowered for marker in CAPTCHA_MARKERS) or looks_blocked(body):
        return Status.BLOCKED, (
            "captcha page from the claim POST — no account signal. "
            "ignorant reports 'not used' here, which is a false negative")
    if "/ax/claim" in str(claim.url):
        return Status.BLOCKED, "redirected to /ax/claim (Amazon's interceptor), no signal"
    return Status.ERROR, (
        f"the claim page carried neither marker (HTTP {claim.status_code}, "
        f"{len(body)} bytes); ignorant reports 'not used' here")
