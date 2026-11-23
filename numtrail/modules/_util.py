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
