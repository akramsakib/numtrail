"""Module registry.

A module is any coroutine that takes a :class:`~numtrail.engine.CheckContext`
and answers a question about the number. The ``@module`` decorator registers it;
discovery imports every package under ``numtrail.modules`` and collects whatever
got registered, so adding support for a site is a single new file.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Mapping

from .models import Result, Status

CheckFn = Callable[["CheckContext"], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    name: str
    check: CheckFn
    domain: str = ""
    method: str = "other"
    semantic: str = "account_exists"
    tier: str = "live"
    summary: str = ""
    requires: tuple[str, ...] = ()          # env vars this module needs
    timeout: float | None = None            # overrides the global timeout
    tags: tuple[str, ...] = ()

    @property
    def needs_credentials(self) -> bool:
        return bool(self.requires)


MODULES: dict[str, ModuleSpec] = {}


def module(
    _fn: CheckFn | None = None,
    *,
    name: str | None = None,
    domain: str = "",
    method: str = "other",
    semantic: str = "account_exists",
    tier: str = "live",
    summary: str = "",
    requires: Iterable[str] = (),
    timeout: float | None = None,
    tags: Iterable[str] = (),
) -> Callable[[CheckFn], CheckFn] | CheckFn:
    """Register a check coroutine. Usable bare or with arguments."""

    def register(fn: CheckFn) -> CheckFn:
        spec = ModuleSpec(
            name=name or fn.__name__,
            check=fn,
            domain=domain,
            method=method,
            semantic=semantic,
            tier=tier,
            summary=summary or (fn.__doc__ or "").strip().split("\n")[0],
            requires=tuple(requires),
            timeout=timeout,
            tags=tuple(tags),
        )
        if spec.name in MODULES:
            raise RuntimeError(f"module {spec.name!r} registered twice")
        MODULES[spec.name] = spec
        return fn

    return register(_fn) if _fn is not None else register


_discovered = False


def discover(force: bool = False) -> dict[str, ModuleSpec]:
    """Import every module package once and return the registry."""
    global _discovered
    if _discovered and not force:
        return MODULES
    from . import modules as modules_pkg

    for info in pkgutil.walk_packages(modules_pkg.__path__, modules_pkg.__name__ + "."):
        importlib.import_module(info.name)
    _discovered = True
    return MODULES


def select(
    only: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
) -> list[ModuleSpec]:
    """Resolve a selection of module names/tags, preserving registry order."""
    specs = discover()
    chosen: Mapping[str, ModuleSpec] = specs

    if only:
        wanted = [n.strip() for n in only if n and n.strip()]
        unknown = [n for n in wanted if n not in specs]
        if unknown:
            raise KeyError(
                f"unknown module(s): {', '.join(unknown)}. "
                f"available: {', '.join(sorted(specs))}"
            )
        chosen = {n: specs[n] for n in wanted}

    out = list(chosen.values())
    if exclude:
        banned = {n.strip() for n in exclude}
        unknown = banned - set(specs)
        if unknown:
            raise KeyError(f"unknown module(s) in exclusion: {', '.join(sorted(unknown))}")
        out = [s for s in out if s.name not in banned]
    if tags:
        wanted_tags = set(tags)
        out = [s for s in out if wanted_tags & set(s.tags)]
    return out


def normalize(raw: object, spec: ModuleSpec) -> Result:
    """Coerce a module's return value into a :class:`Result`."""
    if isinstance(raw, Result):
        return raw
    if isinstance(raw, Status):
        return Result(module=spec.name, domain=spec.domain, status=raw,
                      method=spec.method, semantic=spec.semantic, tier=spec.tier)
    if isinstance(raw, tuple) and raw and isinstance(raw[0], Status):
        detail = raw[1] if len(raw) > 1 else None
        extra = raw[2] if len(raw) > 2 and isinstance(raw[2], dict) else {}
        return Result(module=spec.name, domain=spec.domain, status=raw[0],
                      method=spec.method, semantic=spec.semantic, tier=spec.tier,
                      detail=detail, extra=dict(extra))
    raise TypeError(
        f"module {spec.name!r} returned {type(raw).__name__}; "
        "expected Status, (Status, detail) or Result"
    )
