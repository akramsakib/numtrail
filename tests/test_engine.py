import json
from dataclasses import replace

import httpx
import pytest

from numtrail import Result, Status
from numtrail.engine import CheckContext, Settings, _run_one, run_checks
from numtrail.models import Status as S
from numtrail.phone import parse
from numtrail.registry import MODULES, discover, module, normalize, select

PHONE = "+15550100"


def client_for(handler: httpx.MockTransport):
    return httpx.AsyncClient(transport=handler, timeout=5)


def run(coro):
    import asyncio

    return asyncio.run(coro)


# --------------------------------------------------------------------------- registry
def test_discovery_finds_all_shipped_modules():
    specs = discover()
    assert set(specs) >= {"amazon", "instagram", "snapchat", "twilio",
                          "numverify", "abstractapi", "veriphone"}


def test_select_rejects_unknown_names():
    with pytest.raises(KeyError):
        select(only=["nope"])


def test_select_by_tag():
    names = {s.name for s in select(tags=["social"])}
    assert names == {"instagram", "snapchat"}


def test_duplicate_registration_is_refused():
    @module(name="twice", domain="x.test")
    async def first(ctx):  # pragma: no cover
        return S.ERROR

    with pytest.raises(RuntimeError):
        module(name="twice", domain="y.test")(first)
    MODULES.pop("twice")


def test_normalize_accepts_all_shapes():
    spec = MODULES["instagram"]
    assert normalize(S.FOUND, spec).status is S.FOUND
    res = normalize((S.NOT_FOUND, "why"), spec)
    assert res.status is S.NOT_FOUND and res.detail == "why"
    with pytest.raises(TypeError):
        normalize("found", spec)


# --------------------------------------------------------------------------- engine
def test_instagram_rate_limit_is_reported_not_swallowed():
    transport = httpx.MockTransport(lambda req: httpx.Response(429, text="{}"))
    client = httpx.AsyncClient(transport=transport)
    results = run(run_checks(PHONE, only=["instagram"], client=client,
                             settings=Settings(retries=0)))
    assert len(results) == 1
    assert results[0].status is Status.RATE_LIMITED
    assert "429" in (results[0].detail or "")


def test_missing_key_short_circuits_without_network():
    def explode(request):  # pragma: no cover - must not be reached
        raise AssertionError("network should not be touched without a key")

    client = httpx.AsyncClient(transport=httpx.MockTransport(explode))
    results = run(run_checks(PHONE, only=["twilio"], client=client,
                             settings=Settings(env={})))
    assert results[0].status is Status.NEEDS_CREDENTIALS
    assert "TWILIO_ACCOUNT_SID" in (results[0].detail or "")


def test_results_are_sorted_and_carry_metadata():
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    results = run(run_checks(PHONE, only=["snapchat", "instagram"],
                             client=httpx.AsyncClient(transport=transport),
                             settings=Settings(retries=0)))
    assert [r.module for r in results] == ["instagram", "snapchat"]
    assert all(r.elapsed >= 0 for r in results)
    assert all(r.tier == "legacy" for r in results)


def test_timeout_becomes_error_status():
    import asyncio

    async def slow(ctx):
        await asyncio.sleep(5)

    module(name="slowpoke", domain="slow.test")(slow)
    spec_slow = replace(MODULES["slowpoke"], timeout=0.05)

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        ctx = CheckContext(phone=parse(PHONE), client=client, settings=Settings())
        result = await _run_one(spec_slow, ctx, None)
        await client.aclose()
        return result

    result = run(scenario())
    assert result.status is Status.ERROR
    assert "timed out" in (result.detail or "")
    MODULES.pop("slowpoke")


def test_retries_until_success_then_reports_attempts():
    calls = {"n": 0}

    async def flaky(ctx):
        calls["n"] += 1
        return Status.RATE_LIMITED if calls["n"] < 3 else (Status.FOUND, "third try")

    module(name="flaky", domain="flaky.test")(flaky)
    spec = MODULES["flaky"]

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        ctx = CheckContext(phone=parse(PHONE), client=client,
                           settings=Settings(retries=3, backoff=0))
        out = await _run_one(spec, ctx, None)
        await client.aclose()
        return out

    result = run(scenario())
    assert result.status is Status.FOUND
    assert result.attempts == 3
    MODULES.pop("flaky")


def test_module_exception_is_contained():
    async def broken(ctx):
        raise ValueError("kaboom")

    module(name="broken", domain="broken.test")(broken)
    results = run(run_checks(PHONE, only=["broken"],
                             client=httpx.AsyncClient(transport=httpx.MockTransport(
                                 lambda r: httpx.Response(200))),
                             settings=Settings(retries=0)))
    assert results[0].status is Status.ERROR
    assert "kaboom" in (results[0].detail or "")
    MODULES.pop("broken")


def test_on_result_callback_streams():
    seen = []
    run(run_checks(PHONE, only=["instagram"],
                   client=httpx.AsyncClient(transport=httpx.MockTransport(
                       lambda r: httpx.Response(429))),
                   settings=Settings(retries=0), on_result=seen.append))
    assert [r.module for r in seen] == ["instagram"]


def test_result_dict_round_trips_to_json():
    r = Result(module="m", domain="d", status=Status.FOUND, detail="x", extra={"carrier": "A"})
    payload = json.loads(json.dumps(r.as_dict()))
    assert payload["found"] is True and payload["carrier"] == "A"
