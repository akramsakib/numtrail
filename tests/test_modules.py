"""Each module exercised against recorded-shape responses, no network."""

import asyncio
import base64

import httpx
import pytest

from numtrail.engine import CheckContext, Settings
from numtrail.models import Status
from numtrail.phone import parse
from numtrail.registry import MODULES, discover

PHONE = parse("+15550100")
MY = parse("+60123456789")


def call(name, handler, phone=PHONE, env=None):
    """Run one module against a MockTransport handler.

    Mirrors what the engine does: a missing key becomes ``needs_credentials``
    rather than an exception escaping the module.
    """
    from numtrail.engine import MissingCredential
    from numtrail.models import Result
    from numtrail.registry import normalize

    discover()

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
        ctx = CheckContext(phone=phone, client=client, settings=Settings(env=env or {}))
        spec = MODULES[name]
        try:
            return normalize(await spec.check(ctx), spec)
        except MissingCredential as exc:
            return Result(module=name, domain=spec.domain,
                          status=Status.NEEDS_CREDENTIALS, detail=f"set {exc}")
        finally:
            await client.aclose()

    return asyncio.run(scenario())


# --------------------------------------------------------------------- instagram
def test_instagram_no_users_found_is_not_found():
    r = call("instagram", lambda req: httpx.Response(
        200, json={"message": "No users found", "status": "fail"}))
    assert r.status is Status.NOT_FOUND


def test_instagram_users_present_is_found():
    r = call("instagram", lambda req: httpx.Response(
        200, json={"users": [{"pk": 1}], "status": "ok"}))
    assert r.status is Status.FOUND


def test_instagram_checkpoint_is_blocked():
    r = call("instagram", lambda req: httpx.Response(
        200, json={"message": "checkpoint_required", "status": "fail"}))
    assert r.status is Status.BLOCKED


def test_instagram_sends_hmac_signed_body():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.content.decode()
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"message": "No users found"})

    call("instagram", handler)
    assert "signed_body=" in seen["body"] and "ig_sig_key_version=4" in seen["body"]
    assert "%2215550100%22" in seen["body"] or "15550100" in seen["body"]


# ---------------------------------------------------------------------- snapchat
VALIDATOR_HTML = "<html><body><script src='/v2/app.js'></script></body></html>"


def snapchat_handler(answer, *, prime_cookie="xsrf_token=abc; Path=/", seen=None):
    """ignorant's flow: prime with a GET, then POST the validator."""
    def handler(request: httpx.Request):
        if request.method == "GET":
            headers = {"set-cookie": prime_cookie} if prime_cookie else {}
            return httpx.Response(200, text="<html></html>", headers=headers)
        if seen is not None:
            seen.update(httpx.QueryParams(request.content.decode()))
        return answer
    return handler


def test_snapchat_taken_number_is_found():
    r = call("snapchat", snapchat_handler(httpx.Response(200, json={"status_code": "TAKEN_NUMBER"})))
    assert r.status is Status.FOUND
    assert "TAKEN_NUMBER" in (r.detail or "")


def test_snapchat_ok_is_not_found():
    r = call("snapchat", snapchat_handler(httpx.Response(200, json={"status_code": "OK"})))
    assert r.status is Status.NOT_FOUND


def test_snapchat_unknown_status_code_is_rate_limited():
    r = call("snapchat", snapchat_handler(httpx.Response(200, json={"status_code": "SOMETHING_NEW"})))
    assert r.status is Status.RATE_LIMITED


def test_snapchat_empty_body_is_blocked_not_found():
    """Live behaviour on 2026-11-23: HTTP 200 with a zero-byte body."""
    r = call("snapchat", snapchat_handler(httpx.Response(200, text="")))
    assert r.status is Status.BLOCKED
    assert "empty" in (r.detail or "")


def test_snapchat_without_the_xsrf_cookie_says_so():
    r = call("snapchat", snapchat_handler(httpx.Response(200, json={"status_code": "OK"}),
                                          prime_cookie=""))
    assert r.status is Status.BLOCKED
    assert "xsrf_token" in (r.detail or "")


def test_snapchat_html_instead_of_json_is_blocked():
    r = call("snapchat", snapchat_handler(httpx.Response(200, text=VALIDATOR_HTML)))
    assert r.status is Status.BLOCKED


def test_snapchat_falls_back_to_iso_when_dial_code_unmapped():
    posted = {}
    handler = snapchat_handler(httpx.Response(200, json={"status_code": "OK"}), seen=posted)
    r = call("snapchat", handler, phone=parse("+998901234567"))
    assert posted.get("phone_country_code") == "UZ"
    assert posted.get("phone_number") == "901234567"
    assert r.status is Status.NOT_FOUND


# ------------------------------------------------------------------------ amazon
CLAIM_FORM = """
<html><body><form method="post" action="/ax/claim?arb=abc">
  <input type="hidden" name="appAction" value="SIGNIN_PWD_COLLECT">
  <input type="hidden" name="emailOnlyClaim" value="true">
  <input type="text" name="email" value="">
  <input type="submit" name="go" value="Continue">
</form></body></html>
"""


def test_amazon_password_prompt_is_found():
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text=CLAIM_FORM)
        return httpx.Response(200, text='<input type="password" name="ap_password">')

    r = call("amazon", handler)
    assert r.status is Status.FOUND


def test_amazon_no_account_is_not_found():
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text=CLAIM_FORM)
        return httpx.Response(200, text="<p>We cannot find an account with that number</p>")

    assert call("amazon", handler).status is Status.NOT_FOUND


def test_amazon_406_is_blocked():
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text=CLAIM_FORM)
        return httpx.Response(406, text="")

    r = call("amazon", handler)
    assert r.status is Status.BLOCKED and "406" in (r.detail or "")


def test_amazon_robot_check_is_blocked():
    r = call("amazon", lambda req: httpx.Response(200, text="Enter the characters you see. Captcha"))
    assert r.status is Status.BLOCKED


def test_amazon_posts_the_number_as_the_identifier():
    posted = {}

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text=CLAIM_FORM)
        posted.update(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, text="ap_password")

    call("amazon", handler)
    assert posted.get("email") == "15550100"


# ------------------------------------------------------------------------ twilio
def test_twilio_valid_number_reports_carrier():
    def handler(request: httpx.Request):
        assert request.url.path == "/v2/PhoneNumbers/+15550100"
        auth = request.headers["authorization"]
        assert auth.startswith("Basic ")
        decoded = base64.b64decode(auth.split()[1]).decode()
        assert decoded == "AC123:token"
        return httpx.Response(200, json={
            "valid": True, "country_code": "US",
            "line_type_intelligence": {"carrier_name": "Test Telco", "type": "mobile"}})

    r = call("twilio", handler, env={"TWILIO_ACCOUNT_SID": "AC123",
                                     "TWILIO_AUTH_TOKEN": "token"})
    assert r.status is Status.FOUND
    assert "Test Telco" in (r.detail or "")


def test_twilio_404_is_not_found():
    r = call("twilio", lambda req: httpx.Response(404, json={"code": 20404}),
             env={"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "token"})
    assert r.status is Status.NOT_FOUND


def test_twilio_bad_credentials():
    r = call("twilio", lambda req: httpx.Response(401, json={}),
             env={"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "bad"})
    assert r.status is Status.NEEDS_CREDENTIALS


# --------------------------------------------------------------------- numverify
def test_numverify_valid():
    r = call("numverify", lambda req: httpx.Response(200, json={
        "valid": True, "carrier": "Test Telco", "line_type": "mobile",
        "location": "Kuala Lumpur"}), env={"NUMVERIFY_KEY": "k"})
    assert r.status is Status.FOUND and "Kuala Lumpur" in (r.detail or "")


def test_numverify_error_code_101_is_bad_key():
    r = call("numverify", lambda req: httpx.Response(200, json={
        "error": {"code": 101, "type": "invalid_access_key"}}), env={"NUMVERIFY_KEY": "k"})
    assert r.status is Status.NEEDS_CREDENTIALS


def test_numverify_quota_is_rate_limited():
    r = call("numverify", lambda req: httpx.Response(200, json={
        "error": {"code": 104, "type": "usage_limit_reached"}}), env={"NUMVERIFY_KEY": "k"})
    assert r.status is Status.RATE_LIMITED


# ------------------------------------------------------------------ abstractapi
def test_abstractapi_invalid_number():
    r = call("abstractapi", lambda req: httpx.Response(200, json={
        "valid": False, "phone": "15550100"}),
        env={"ABSTRACTAPI_PHONE_KEY": "k"})
    assert r.status is Status.NOT_FOUND


# -------------------------------------------------------------------- veriphone
def test_veriphone_valid():
    r = call("veriphone", lambda req: httpx.Response(200, json={
        "status": "success", "phone_valid": True, "carrier": "Test Telco",
        "phone_type": "mobile", "phone_region": "Malaysia"}),
        phone=MY, env={"VERIPHONE_KEY": "k"})
    assert r.status is Status.FOUND and "Malaysia" in (r.detail or "")


def test_veriphone_error_payload():
    r = call("veriphone", lambda req: httpx.Response(200, json={
        "status": "error", "message": "invalid key"}), env={"VERIPHONE_KEY": "k"})
    assert r.status is Status.NEEDS_CREDENTIALS


@pytest.mark.parametrize("name", ["instagram", "snapchat", "amazon",
                                  "twilio", "numverify", "abstractapi", "veriphone",
                                  "vonage", "ipqualityscore", "telesign", "plivo",
                                  "bird", "numlookupapi", "trestle", "mocean"])
def test_every_module_survives_garbage(name):
    r = call(name, lambda req: httpx.Response(200, text="<not json>"), env=ALL_KEYS)
    assert isinstance(r.status, Status)


# ------------------------------------------------- regressions from live probing
SNAP_LANDING = ('<html><script>var i18n={"BvxEqE":"Unable to verify captcha. '
                'Please try again.","C/c3wR":"Enter your current password"};</script></html>')


def test_snapchat_does_not_mistake_i18n_for_a_botwall():
    """A page that merely mentions captcha is not a captcha page."""

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text=SNAP_LANDING,
                                  headers={"set-cookie": "xsrf_token=abc; Path=/"})
        return httpx.Response(200, json={"status_code": "TAKEN_NUMBER"})

    r = call("snapchat", handler)
    assert r.status is Status.FOUND, r.detail


AMAZON_CAPTCHA_PAGE = """
<html><body><h4>Enter the characters you see below</h4>
<form method="get" action="/errors_page/validateCaptcha">
  <input type="hidden" name="amzn" value="x"><input type="hidden" name="amzn-r" value="/">
</form></body></html>
"""


def test_amazon_captcha_interstitial_is_blocked_not_not_found():
    r = call("amazon", lambda req: httpx.Response(200, text=AMAZON_CAPTCHA_PAGE))
    assert r.status is Status.BLOCKED
    assert "captcha" in (r.detail or "")


METHODLESS_FORM = """
<html><body><form action="/ax/claim?arb=abc">
  <input type="hidden" name="appAction" value="SIGNIN_PWD_COLLECT">
  <input type="text" name="email" value="">
</form></body></html>
"""


def test_amazon_form_without_method_attribute_is_still_parsed():
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text=METHODLESS_FORM)
        return httpx.Response(200, text='<input type="password" name="ap_password">')

    assert call("amazon", handler).status is Status.FOUND


def test_amazon_generic_apology_string_is_not_treated_as_not_found():
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, text=METHODLESS_FORM)
        return httpx.Response(200, text="<p>Sorry, there was a problem.</p>")

    assert call("amazon", handler).status is Status.ERROR


AMAZON_CAPTCHA_FORM = """
<html><body><form method="get" action="/errors_page/validateCaptcha">
  <input type="hidden" name="amzn" value="x"><input type="hidden" name="amzn-r" value="/">
  <input type="hidden" name="amzn-pt" value=""><input type="hidden" name="field-keywords" value="">
</form></body></html>
"""


def test_amazon_robot_check_signature_is_blocked():
    page = ("<html><!-- To discuss automated access to Amazon data please contact "
            "api-services-support@amazon.com. --></html>")
    assert call("amazon", lambda req: httpx.Response(200, text=page)).status is Status.BLOCKED


# ------------------------------------------------- keyed API providers (batch 2)
ALL_KEYS = {
    "TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "t",
    "NUMVERIFY_KEY": "k", "ABSTRACTAPI_PHONE_KEY": "k", "VERIPHONE_KEY": "k",
    "IPQS_KEY": "k", "VONAGE_API_KEY": "k", "VONAGE_API_SECRET": "s",
    "TELESIGN_CUSTOMER_ID": "c", "TELESIGN_API_KEY": "k",
    "PLIVO_AUTH_ID": "id", "PLIVO_AUTH_TOKEN": "t", "BIRD_ACCESS_KEY": "k",
    "NUMLOOKUPAPI_KEY": "k", "TRESTLE_API_KEY": "k",
    "MOCEAN_API_KEY": "k", "MOCEAN_API_SECRET": "s",
}


def test_vonage_success_is_found():
    r = call("vonage", lambda req: httpx.Response(200, json={
        "status": 0, "country_code": "US", "national_format": "(555) 010-0"}),
        env=ALL_KEYS)
    assert r.status is Status.FOUND and "US" in (r.detail or "")


def test_vonage_body_status_3_is_not_found():
    r = call("vonage", lambda req: httpx.Response(200, json={
        "status": 3, "error_text": "invalid params"}), env=ALL_KEYS)
    assert r.status is Status.NOT_FOUND


def test_vonage_status_4_is_bad_credentials():
    r = call("vonage", lambda req: httpx.Response(200, json={
        "status": 4, "error_text": "bad credentials"}), env=ALL_KEYS)
    assert r.status is Status.NEEDS_CREDENTIALS


def test_ipqs_reports_active_line():
    r = call("ipqualityscore", lambda req: httpx.Response(200, json={
        "success": True, "valid": True, "active": True, "carrier": "Test Telco",
        "line_type": "Wireless", "fraud_score": 12}), env=ALL_KEYS)
    assert r.status is Status.FOUND
    assert "active=True" in (r.detail or "") and "fraud=12" in (r.detail or "")


def test_ipqs_bad_key_is_needs_credentials():
    r = call("ipqualityscore", lambda req: httpx.Response(200, json={
        "success": False, "message": "Invalid or unauthorized key. Please check the API key"}),
        env=ALL_KEYS)
    assert r.status is Status.NEEDS_CREDENTIALS


def test_telesign_code_300_is_found():
    r = call("telesign", lambda req: httpx.Response(200, json={
        "status": {"code": 300, "description": "Transaction successfully completed"},
        "phone_type": {"description": "MOBILE"}, "carrier": {"name": "Test Telco"}}),
        env=ALL_KEYS)
    assert r.status is Status.FOUND and "MOBILE" in (r.detail or "")


def test_telesign_code_101_is_not_found():
    r = call("telesign", lambda req: httpx.Response(200, json={
        "status": {"code": 101, "description": "Transaction not completed"}}), env=ALL_KEYS)
    assert r.status is Status.NOT_FOUND


def test_plivo_404_is_not_found():
    assert call("plivo", lambda req: httpx.Response(404, json={}),
                env=ALL_KEYS).status is Status.NOT_FOUND


def test_plivo_success_reports_carrier():
    r = call("plivo", lambda req: httpx.Response(200, json={
        "carrier": {"name": "Test Telco"}, "country": "US"}), env=ALL_KEYS)
    assert r.status is Status.FOUND and "Test Telco" in (r.detail or "")


def test_bird_sends_access_key_header():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"countryCode": "MY", "carrier": "Test",
                                         "type": "mobile"})

    r = call("bird", handler, env=ALL_KEYS)
    assert seen["auth"].startswith("AccessKey ")
    assert r.status is Status.FOUND


def test_bird_401_needs_credentials():
    assert call("bird", lambda req: httpx.Response(401, json={}),
                env=ALL_KEYS).status is Status.NEEDS_CREDENTIALS


def test_numlookupapi_valid():
    r = call("numlookupapi", lambda req: httpx.Response(200, json={
        "valid": True, "country_name": "Malaysia", "carrier": "Test"}),
        phone=MY, env=ALL_KEYS)
    assert r.status is Status.FOUND and "Malaysia" in (r.detail or "")


def test_numlookupapi_missing_key_error_payload():
    r = call("numlookupapi", lambda req: httpx.Response(401, json={
        "message": "No API key found in request",
        "error": {"code": "missing_api_key"}}), env=ALL_KEYS)
    assert r.status is Status.NEEDS_CREDENTIALS


def test_trestle_reports_prepaid():
    r = call("trestle", lambda req: httpx.Response(200, json={
        "is_valid": True, "carrier": "Test", "line_type": "Mobile",
        "country": "US", "is_prepaid": True}), env=ALL_KEYS)
    assert r.status is Status.FOUND and "prepaid" in (r.detail or "")


def test_trestle_invalid_is_not_found():
    r = call("trestle", lambda req: httpx.Response(200, json={"is_valid": False}),
             env=ALL_KEYS)
    assert r.status is Status.NOT_FOUND


def test_mocean_success():
    r = call("mocean", lambda req: httpx.Response(200, json={
        "status": 0, "country": "MY", "carrier": "Test"}), phone=MY, env=ALL_KEYS)
    assert r.status is Status.FOUND and "MY" in (r.detail or "")


def test_mocean_auth_error_payload():
    r = call("mocean", lambda req: httpx.Response(200, json={
        "status": 1, "err_msg": "auth failed"}), env=ALL_KEYS)
    assert r.status is Status.NEEDS_CREDENTIALS


def test_all_keyed_modules_need_a_key_without_env():
    for name in ("vonage", "ipqualityscore", "telesign", "plivo", "bird",
                 "numlookupapi", "trestle", "mocean"):
        r = call(name, lambda req: httpx.Response(200, json={}), env={})
        assert r.status is Status.NEEDS_CREDENTIALS, name


# --------------------------------------------------- offline Malaysian numbering
from numtrail.modules.offline.my_number import decode as my_decode, formats as my_formats


@pytest.mark.parametrize("national,needle", [
    ("123456789", "012 block"),
    ("1112345678", "011 universal"),
    ("102361234", "010 block"),
    ("188881234", "018 block"),
    ("162345678", "016 block"),
    ("321123456", "fixed line"),
    ("82212345", "Sarawak"),
])
def test_my_number_decodes_blocks(national, needle):
    status, detail = my_decode(national)
    assert status is Status.FOUND, detail
    assert needle in detail


@pytest.mark.parametrize("national", ["12345", "11123456", "9912345678", "12345678901"])
def test_my_number_rejects_impossible_lengths(national):
    status, detail = my_decode(national)
    assert status is Status.NOT_FOUND, detail


def test_my_number_formats():
    f = my_formats("123456789")
    assert f["e164"] == "+60123456789"
    assert f["local"] == "012-345 6789"
    assert f["wa_me"].endswith("60123456789")


def test_my_number_module_is_offline_only():
    r = call("my_number", lambda req: (_ for _ in ()).throw(AssertionError("network!")),
             phone=MY)
    assert r.status is Status.FOUND
    assert "012 block" in (r.detail or "")


def test_my_number_module_skips_other_countries():
    r = call("my_number", lambda req: pytest.fail("no network allowed"), phone=PHONE)
    assert r.status is Status.SKIPPED
