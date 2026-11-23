"""Amazon: probe the sign-in claim flow with a phone number as the identifier.

Ported from megadose/ignorant (GPL-3.0), then re-aimed at Amazon's current
``/ax/claim`` flow. Amazon treats a scripted sign-in attempt as a robot fairly
quickly (HTTP 406 on the claim POST), so ``blocked`` is a normal result here;
``NUMTRAIL_AMAZON_COOKIE`` can be supplied to replay a browser session.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from ...engine import CheckContext
from ...models import Status
from ...registry import module
from .._util import classify_transport, looks_blocked

BASE = "https://www.amazon.com"
SIGNIN_URL = (
    BASE + "/ap/signin?openid.pape.max_auth_age=900"
    "&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F"
    "&openid.assoc_handle=usflex&openid.mode=checkid_setup"
    "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
    "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
    "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
)

NO_ACCOUNT_MARKERS = (
    "we cannot find an account with that",
    "cannot find an account with that",
    "no account found with that",
    "we could not find an account",
)
PASSWORD_MARKERS = ('name="ap_password"', 'id="ap_password"', "auth-password-missing-alert")


def _form_fields(html: str) -> tuple[str | None, dict[str, str]]:
    """Pull the first POST form's action and hidden inputs out of the page."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for form in soup.find_all("form"):
        fields = {
            field["name"]: field.get("value", "")
            for field in form.select("input[name]")
            if field.get("type") != "submit"
        }
        if not fields:
            continue
        # Amazon omits method= on the claim form, so rank by content instead:
        # the real one carries the identifier field or the claim action.
        score = 0
        if "email" in fields:
            score += 2
        if "appAction" in fields:
            score += 2
        if (form.get("action") or "").startswith(("/ax/claim", "/ap/")):
            score += 1
        if (form.get("method") or "").lower() == "post":
            score += 1
        if score < 2:      # captcha interstitials carry amzn/amzn-r, nothing else
            continue
        candidates.append((score, form.get("action"), fields))
    if not candidates:
        return None, {}
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, action, fields = candidates[0]
    return action, fields


def _absolute(action: str | None, base: str) -> str:
    if not action:
        return base
    if action.startswith("http"):
        return action
    if action.startswith("/"):
        return BASE + action
    return base.rstrip("/") + "/" + action


@module(
    name="amazon",
    domain="amazon.com",
    method="login",
    tier="legacy",
    tags=("shopping",),
    summary="sign-in flow with the number as the account identifier",
)
async def amazon(ctx: CheckContext):
    cookie = ctx.settings.credential("NUMTRAIL_AMAZON_COOKIE")
    headers = {"Referer": BASE + "/", "Accept-Language": "en-US,en;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie

    page = await ctx.get(SIGNIN_URL, headers=headers)
    transport = classify_transport(page)
    if transport is not None:
        return transport, f"HTTP {page.status_code} on the sign-in page"
    if (looks_blocked(page.text) or "/errors_page/validateCaptcha" in page.text
            or "api-services-support@amazon.com" in page.text):
        return Status.BLOCKED, "Amazon served its captcha interstitial"

    action, fields = _form_fields(page.text)
    if not fields:
        return Status.BLOCKED, "no claim form on the page (bot-wall or new layout)"
    if "validateCaptcha" in (action or ""):
        return Status.BLOCKED, "the form on offer is the captcha validator"

    fields["email"] = ctx.phone.digits
    fields.setdefault("appAction", "SIGNIN_PWD_COLLECT")

    claim = await ctx.post(_absolute(action, page.url), data=fields,
                           headers={**headers, "Referer": str(page.url)})
    if claim.status_code == 406:
        return Status.BLOCKED, "claim POST rejected (406) - needs a browser session"
    transport = classify_transport(claim)
    if transport is not None:
        return transport, f"HTTP {claim.status_code} on the claim POST"

    if "errors_page" in str(claim.url) or "errors_page" in (claim.headers.get("location") or ""):
        return Status.BLOCKED, "claim redirected to Amazon's error/captcha page"

    body = claim.text.lower()
    if any(marker in body for marker in PASSWORD_MARKERS):
        return Status.FOUND, "password prompt returned for this number"
    if any(marker in body for marker in NO_ACCOUNT_MARKERS):
        return Status.NOT_FOUND, "Amazon does not recognise this number"
    if looks_blocked(body) or "/errors_page/validateCaptcha" in body:
        return Status.BLOCKED, "captcha on the claim response"
    return Status.ERROR, f"inconclusive response (HTTP {claim.status_code})"
