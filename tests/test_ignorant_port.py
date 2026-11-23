"""Parity with megadose/ignorant, module by module.

ignorant 1.2 ships exactly three checks: snapchat, instagram, amazon. This file
pins the parts that must stay identical (endpoints, bodies, signature, dial map)
and the two verdicts we deliberately do not copy (Amazon's captcha read as "not
used", Instagram's spam 429 read as "used").
"""

import asyncio
import hashlib
import hmac
import json
import urllib.parse

import httpx
import pytest

from numtrail.engine import CheckContext, Settings
from numtrail.models import Status
from numtrail.modules.ignorant import DIAL_TO_TERRITORY, IGNORANT_MODULES
from numtrail.modules.legacy import amazon as amazon_mod
from numtrail.modules.legacy import instagram as instagram_mod
from numtrail.modules.legacy import snapchat as snapchat_mod
from numtrail.phone import parse
from numtrail.registry import MODULES, discover

PHONE = parse("+60123456789")


def call(name, handler, phone=PHONE):
    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
        ctx = CheckContext(phone=phone, client=client, settings=Settings(env={}))
        from numtrail.registry import normalize
        discover()
        try:
            return normalize(await MODULES[name].check(ctx), MODULES[name])
        finally:
            await client.aclose()

    return asyncio.run(scenario())


# ------------------------------------------------------------------ the whole set
def test_ignorant_ships_three_modules_and_we_have_all_three():
    assert IGNORANT_MODULES == ("snapchat", "instagram", "amazon")
    discover()
    for name in IGNORANT_MODULES:
        assert name in MODULES, f"{name} missing — that would not be a full port"


# --------------------------------------------------------------------- endpoints
def test_instagram_endpoint_and_key_are_ignorants():
    assert instagram_mod.LOOKUP_URL == "https://i.instagram.com/api/v1/users/lookup/"
    assert instagram_mod.SIG_KEY_VERSION == "4"
    assert instagram_mod.IG_SIG_KEY == (
        "e6358aeede676184b9fe702b30f4fd35e71744605e39d2181a34cede076b3c33")
    assert instagram_mod.IGNORANT_UA == "Instagram 101.0.0.15.120"


def test_instagram_body_is_the_signed_form_ignorant_sends():
    payload = instagram_mod.generate_data("60123456789")
    assert payload == {
        "login_attempt_count": "0",
        "directly_sign_in": "true",
        "source": "default",
        "q": "60123456789",
        "ig_sig_key_version": "4",
    }
    body = instagram_mod.generate_signature(payload)
    raw = json.dumps(payload)
    expected = hmac.new(instagram_mod.IG_SIG_KEY.encode(), raw.encode(),
                        hashlib.sha256).hexdigest()
    assert body.startswith("ig_sig_key_version=4&signed_body=")
    signed = body.split("signed_body=", 1)[1]
    digest, _, quoted = signed.partition(".")
    assert digest == expected
    assert json.loads(urllib.parse.unquote_plus(quoted)) == payload


def test_instagram_sends_the_dial_code_plus_subscriber_digits():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"message": "No users found", "status": "fail"})

    call("instagram", handler)
    quoted = seen["body"].split("signed_body=", 1)[1].split(".", 1)[1]
    assert json.loads(urllib.parse.unquote_plus(quoted))["q"] == "60123456789"


def test_snapchat_endpoints_match_ignorant():
    assert snapchat_mod.BASE == "https://accounts.snapchat.com"
    assert snapchat_mod.VALIDATE_URL == "https://accounts.snapchat.com/accounts/validate_phone_number"
    assert snapchat_mod.IGNORANT_HEADERS["Origin"] == "https://accounts.snapchat.com"
    assert snapchat_mod.IGNORANT_HEADERS["Content-Type"].startswith("application/x-www-form-urlencoded")


def test_amazon_urls_match_ignorant():
    assert amazon_mod.SIGNIN_URL.startswith("https://www.amazon.com/ap/signin?openid.pape.max_auth_age=0")
    assert "openid.assoc_handle=usflex" in amazon_mod.SIGNIN_URL
    assert "openid.return_to=https%3A%2F%2Fwww.amazon.com%2F%3F_encoding%3DUTF8" in amazon_mod.SIGNIN_URL
    assert amazon_mod.POST_URL == "https://www.amazon.com/ap/signin/"


# ---------------------------------------------------------------- dial → territory
def test_snapchat_dial_map_is_ignorants_full_table():
    assert len(DIAL_TO_TERRITORY) == 205
    # the odd but intentional entries in ignorant's own table
    assert DIAL_TO_TERRITORY["1"] == "VI"
    assert DIAL_TO_TERRITORY["44"] == "JE"
    assert DIAL_TO_TERRITORY["358"] == "FI"
    assert DIAL_TO_TERRITORY["39"] == "VA"
    assert DIAL_TO_TERRITORY["47"] == "SJ"
    assert DIAL_TO_TERRITORY["61"] == "CX"
    assert DIAL_TO_TERRITORY["290"] == "TA"
    assert DIAL_TO_TERRITORY["60"] == "MY"


def test_snapchat_posts_territory_and_bare_subscriber_digits():
    seen = {}

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text="", headers={"set-cookie": "xsrf_token=t; Path=/"})
        seen.update(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"status_code": "OK"})

    call("snapchat", handler)
    assert seen["phone_country_code"] == "MY"
    assert seen["phone_number"] == "123456789"      # no dial code, no leading zero
    assert seen["xsrf_token"] == "t"


# ------------------------------------------------------- the two verdicts we refuse
@pytest.mark.parametrize("page, expected", [
    # Amazon's captcha page: ignorant reads "no password prompt" as "not used"
    ('<html><body><h4>Enter the characters you see</h4>'
     '<form action="/errors_page/validateCaptcha"><input name="amzn" value="x"></form>'
     '</body></html>', Status.BLOCKED),
    ('<html>Click the button below to continue shopping</html>', Status.BLOCKED),
    # a real answer, either way
    ('<html><div id="auth-password-missing-alert">Enter your password</div></html>',
     Status.FOUND),
    ('<html><div>We cannot find an account with that email address</div></html>',
     Status.NOT_FOUND),
    # nothing on the page: report that, never "not used"
    ('<html><body><h1>Amazon</h1><p>Continue</p></body></html>', Status.ERROR),
])
def test_amazon_verdicts(page, expected):
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text='<form action="/ap/signin/">'
                                            '<input name="appAction" value="SIGNIN_PWD_COLLECT">'
                                            '<input name="email" value=""></form>')
        return httpx.Response(200, text=page)

    assert call("amazon", handler).status is expected


def test_amazon_posts_the_number_as_the_email_field():
    posted = {}

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text='<form action="/ap/signin/">'
                                            '<input name="email" value="">'
                                            '<input name="appAction" value="SIGNIN_PWD_COLLECT"></form>')
        posted.update(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, text="<div>nothing to see</div>")

    call("amazon", handler)
    assert posted["email"] == "60123456789"
    assert posted["appAction"] == "SIGNIN_PWD_COLLECT"


def test_amazon_form_inputs_matches_ignorants_dict_comprehension():
    html = ('<form><input name="a" value="1"><input name="b"><input value="nope">'
            '<input name="c" value=""></form>')
    assert amazon_mod.form_inputs(html) == {"a": "1", "c": ""}


def test_instagram_spam_429_is_not_reported_as_found():
    """ignorant: any 200 that is not 'No users found' means 'used'."""
    r = call("instagram", lambda req: httpx.Response(
        200, json={"message": "", "spam": True, "status": "fail"}))
    assert r.status is Status.RATE_LIMITED
    assert "ignorant would report 'used'" in (r.detail or "")


def test_instagram_unknown_payload_is_not_reported_as_found():
    r = call("instagram", lambda req: httpx.Response(200, text="<html>nope</html>"))
    assert r.status is Status.BLOCKED
