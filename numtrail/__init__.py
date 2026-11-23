"""numtrail — trace where a phone number is registered.

    >>> from numtrail import check
    >>> [r.module for r in check("+15550100", only=["numverify"])]
    ['numverify']

The library and the CLI share one engine, so anything the command line can do is
available from Python:

    import asyncio
    from numtrail import Settings, run_checks

    async def main():
        results = await run_checks("+60123456789", settings=Settings(timeout=8))
        for r in results:
            print(r.module, r.status.value, r.detail or "")

    asyncio.run(main())
"""

from __future__ import annotations

from .engine import (
    CheckContext,
    MissingCredential,
    Settings,
    check,
    run_checks,
)
from .models import Result, Status
from .phone import InvalidPhone, Phone, parse
from .registry import MODULES, ModuleSpec, discover, module, select

__version__ = "0.1.0"

__all__ = [
    "CheckContext",
    "InvalidPhone",
    "MODULES",
    "MissingCredential",
    "ModuleSpec",
    "Phone",
    "Result",
    "Settings",
    "Status",
    "__version__",
    "check",
    "discover",
    "module",
    "parse",
    "run_checks",
    "select",
]
