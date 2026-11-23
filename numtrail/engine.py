"""Check engine: opens one client, runs the selected modules, retries politely."""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Mapping, Sequence

import httpx

from .models import Result, Status
from .phone import Phone, parse
from .registry import ModuleSpec, normalize, select
from .ua import browser_headers

__all__ = ["Settings", "CheckContext", "MissingCredential", "run_checks", "check"]


class MissingCredential(RuntimeError):
    """Raise from a module when a required env var / API key is absent."""


@dataclass
class Settings:
    timeout: float = 15.0
    retries: int = 1
    concurrency: int = 8
    backoff: float = 0.6
    proxy: str | None = None
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def credential(self, name: str) -> str | None:
        value = self.env.get(name)
        return value.strip() if value and value.strip() else None


@dataclass
class CheckContext:
    """Everything a module is allowed to touch."""

    phone: Phone
    client: httpx.AsyncClient
    settings: Settings

    async def get(self, url: str, **kw) -> httpx.Response:
        kw.setdefault("follow_redirects", True)
        return await self.client.get(url, **kw)

    async def post(self, url: str, **kw) -> httpx.Response:
        kw.setdefault("follow_redirects", True)
        return await self.client.post(url, **kw)

    def credential(self, name: str) -> str:
        value = self.settings.credential(name)
        if value is None:
            raise MissingCredential(name)
        return value


def _build_client(settings: Settings) -> httpx.AsyncClient:
    limits = httpx.Limits(max_connections=settings.concurrency * 4,
                          max_keepalive_connections=settings.concurrency)
    kwargs: dict = {
        "timeout": settings.timeout,
        "limits": limits,
        "headers": browser_headers(),
        "http2": False,
    }
    if settings.proxy:
        kwargs["proxy"] = settings.proxy
    return httpx.AsyncClient(**kwargs)


async def _run_one(spec: ModuleSpec, ctx: CheckContext,
                   on_result: Callable[[Result], None] | None) -> Result:
    attempts = max(1, ctx.settings.retries + 1)
    timeout = spec.timeout or ctx.settings.timeout
    result: Result | None = None

    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        if spec.requires and not any(ctx.settings.credential(k) for k in spec.requires):
            result = Result(module=spec.name, domain=spec.domain,
                            status=Status.NEEDS_CREDENTIALS, method=spec.method,
                            semantic=spec.semantic, tier=spec.tier,
                            detail="set " + " or ".join(spec.requires),
                            attempts=attempt)
            break
        try:
            raw = await asyncio.wait_for(spec.check(ctx), timeout=timeout)
            result = normalize(raw, spec)
        except asyncio.TimeoutError:
            result = Result(module=spec.name, domain=spec.domain, status=Status.ERROR,
                            method=spec.method, semantic=spec.semantic, tier=spec.tier,
                            detail=f"timed out after {timeout:g}s")
        except MissingCredential as exc:
            result = Result(module=spec.name, domain=spec.domain,
                            status=Status.NEEDS_CREDENTIALS, method=spec.method,
                            semantic=spec.semantic, tier=spec.tier,
                            detail=f"set {exc}")
        except Exception as exc:  # noqa: BLE001 - a module must never kill the run
            name = type(exc).__name__
            result = Result(module=spec.name, domain=spec.domain, status=Status.ERROR,
                            method=spec.method, semantic=spec.semantic, tier=spec.tier,
                            detail=f"{name}: {exc}"[:240])

        result.elapsed = time.perf_counter() - started
        result.attempts = attempt
        result.module = result.module or spec.name
        result.domain = result.domain or spec.domain

        if attempt >= attempts or not result.status.retryable:
            break
        await asyncio.sleep(ctx.settings.backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.25))

    assert result is not None
    if on_result is not None:
        on_result(result)
    return result


async def run_checks(
    number: str | Phone,
    country_code: str | None = None,
    *,
    only: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    client: httpx.AsyncClient | None = None,
    on_result: Callable[[Result], None] | None = None,
    **_ignored,
) -> list[Result]:
    """Run every selected module against one number.

    ``transport``/``client`` are injection points for tests; production code can
    ignore them. Results are sorted by module name.
    """
    phone = number if isinstance(number, Phone) else parse(str(number), country_code)
    settings = settings or Settings()
    specs = select(only=only, exclude=exclude, tags=tags)

    owns_client = client is None
    if client is None:
        if transport is not None:
            client = httpx.AsyncClient(transport=transport, timeout=settings.timeout,
                                       headers=browser_headers())
        else:
            client = _build_client(settings)

    ctx = CheckContext(phone=phone, client=client, settings=settings)
    semaphore = asyncio.Semaphore(max(1, settings.concurrency))

    async def guarded(spec: ModuleSpec) -> Result:
        async with semaphore:
            return await _run_one(spec, ctx, on_result)

    try:
        results = await asyncio.gather(*(guarded(s) for s in specs))
    finally:
        if owns_client:
            await client.aclose()

    return sorted(results, key=lambda r: r.module)


def check(number: str, country_code: str | None = None,
          **kwargs) -> list[Result]:
    """Blocking convenience wrapper around :func:`run_checks`."""
    return asyncio.run(run_checks(number, country_code, **kwargs))
