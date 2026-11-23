"""Helpers shared by modules, so site-specific files stay short."""

from __future__ import annotations

import httpx

from ..models import Status

# Deliberately specific. A bare "captcha" substring is useless: Snapchat ships an
# i18n dictionary containing "Unable to verify captcha" on every page, and pages
# embed reCAPTCHA widgets routinely. These phrases only show up on challenge
# interstitials.
BOTWALL_MARKERS = (
    "unusual traffic",
    "are you a robot",
    "verify you are human",
    "checking your browser before accessing",
    "checking if the site connection is secure",
    "cf-challenge",
    "px-captcha",
    "validatecaptcha",
    "enter the characters you see",
    "request unsuccessful. incapsula",
    "access to this page has been denied",
    "sorry/index",
)

LOGINWALL_MARKERS = ("log in to continue", "sign in to continue", "login_required")


def looks_blocked(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in BOTWALL_MARKERS)


def looks_login_walled(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in LOGINWALL_MARKERS)


def classify_transport(response: httpx.Response) -> Status | None:
    """Map transport-level failures onto our vocabulary.

    Returns ``None`` when the response is usable by the module.
    """
    code = response.status_code
    if code == 429:
        return Status.RATE_LIMITED
    if code in (401, 403):
        return Status.BLOCKED
    if code in (503, 521, 522, 524):
        return Status.BLOCKED
    if code >= 500:
        return Status.ERROR
    return None


def first_json(response: httpx.Response) -> dict:
    """``response.json()`` that returns ``{}`` instead of raising."""
    try:
        data = response.json()
    except Exception:  # noqa: BLE001 - HTML where JSON was promised
        return {}
    return data if isinstance(data, dict) else {}


APP_SHELL_MARKERS = (
    '<div id="react-root"', '<div id="root"></div>', "__next_data__",
    '<div id="app"></div>', 'window.__nuxt__', '<app-root',
)


def looks_like_app_shell(text: str, *, min_text: int = 400) -> bool:
    """True when the body is a client-rendered shell, not a server-rendered page.

    Matters for "no error string on the page -> the account exists" checks: a SPA
    returns the same shell for every profile URL, so that inference is worthless
    there. Better to say nothing than to claim every username exists.
    """
    lowered = text.lower()
    if not any(marker in lowered for marker in APP_SHELL_MARKERS):
        return False
    # strip scripts/styles, then see how much real text is left
    import re
    stripped = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text,
                      flags=re.S | re.I)
    visible = re.sub(r"<[^>]+>", " ", stripped)
    return len(" ".join(visible.split())) < min_text
