"""Build a results snapshot inside GitHub Actions.

This is the "backend on GitHub" path: GitHub Actions does the probing on a
schedule, and the results are committed back into the repository, where the
static Pages site reads them. No server, no third-party account, no token in a
browser.

The trade-off, stated plainly: it answers for the numbers in ``watchlist.txt``,
not for arbitrary visitor input. Triggering a workflow from a web page needs a
token with `workflow` scope, and putting that in client-side JS would hand
anyone write access to your repo. A watchlist sidesteps that entirely — and it
is also the more responsible shape, because the site stays a viewer rather than
an open enumeration service.

    python -m numtrail.snapshot --watchlist watchlist.txt \\
        --out numtrail/web/static/snapshots.json

Keys/proxies come from the environment, so repository secrets work directly:
``NUMVERIFY_KEY``, ``VERIPHONE_KEY``, ``PROXY_URL`` …
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .engine import Settings, run_checks
from .models import Result, Status
from .phone import InvalidPhone, parse
from .registry import discover, select

__all__ = ["read_watchlist", "build_snapshot", "write_snapshot", "main"]

WATCHLIST_KEYS = ("NUMVERIFY_KEY", "VERIPHONE_KEY", "ABSTRACTAPI_PHONE_KEY",
                  "IPQS_KEY", "NUMLOOKUPAPI_KEY", "TRESTLE_API_KEY",
                  "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                  "VONAGE_API_KEY", "TELESIGN_CUSTOMER_ID", "PLIVO_AUTH_ID",
                  "BIRD_ACCESS_KEY", "MOCEAN_API_KEY")


def read_watchlist(path: Path) -> list[str]:
    """One number per line; ``#`` comments and blanks ignored."""
    numbers: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        numbers.append(line)
    return numbers


async def build_snapshot(numbers: Iterable[str], *, settings: Settings | None = None,
                         only: Iterable[str] | None = None,
                         concurrency: int = 4) -> dict:
    """Probe every watchlisted number and return the JSON-ready snapshot."""
    discover()
    settings = settings or Settings(timeout=20.0, retries=1, concurrency=concurrency)

    entries: dict[str, dict] = {}
    errors: list[str] = []

    for raw in numbers:
        try:
            phone = parse(raw)
        except InvalidPhone as exc:
            errors.append(f"{raw}: {exc}")
            continue

        results = await run_checks(phone, only=only, settings=settings)
        conclusive = [r for r in results if r.conclusive]
        entries[phone.e164] = {
            "iso": phone.iso,
            "pretty": phone.pretty(),
            "probed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "conclusive": len(conclusive),
            "found": [r.domain for r in results if r.found],
            "results": [
                {
                    "module": r.module,
                    "domain": r.domain,
                    "status": r.status.value,
                    "found": r.found,
                    "detail": r.detail,
                    "semantic": r.semantic,
                    "elapsed": round(r.elapsed, 3),
                }
                for r in results
            ],
        }

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "numtrail.snapshot",
        "watchlist_size": len(entries),
        "credentials_present": sorted(k for k in WATCHLIST_KEYS if os.environ.get(k)),
        "proxy": bool(os.environ.get("PROXY_URL") or os.environ.get("HTTPS_PROXY")),
        "entries": entries,
        "skipped_lines": errors,
    }


def write_snapshot(snapshot: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return path


def summarize(snapshot: dict) -> str:
    lines = [
        f"snapshot {snapshot['generated_at']} · {snapshot['watchlist_size']} numbers · "
        f"credentials: {', '.join(snapshot['credentials_present']) or 'none'} · "
        f"proxy: {'yes' if snapshot['proxy'] else 'no'}"
    ]
    for number, entry in snapshot["entries"].items():
        tally: dict[str, int] = {}
        for r in entry["results"]:
            tally[r["status"]] = tally.get(r["status"], 0) + 1
        compact = " ".join(f"{k}={v}" for k, v in sorted(tally.items()))
        lines.append(f"  {number:16} conclusive={entry['conclusive']:<2} {compact}")
        for domain in entry["found"]:
            lines.append(f"      found: {domain}")
    for bad in snapshot["skipped_lines"]:
        lines.append(f"  skipped line: {bad}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="numtrail.snapshot",
                                     description="Probe a watchlist and write a JSON snapshot")
    parser.add_argument("--watchlist", default="watchlist.txt")
    parser.add_argument("--out", default="numtrail/web/static/snapshots.json")
    parser.add_argument("--modules", default=None, help="comma separated subset")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(list(argv) if argv is not None else None)

    watchlist = Path(args.watchlist)
    if not watchlist.is_file():
        print(f"no watchlist at {watchlist} — create one, one number per line")
        return 2

    numbers = read_watchlist(watchlist)
    if not numbers:
        print(f"{watchlist} has no numbers in it")
        return 2

    only = [m.strip() for m in args.modules.split(",")] if args.modules else None
    snapshot = asyncio.run(build_snapshot(
        numbers,
        settings=Settings(timeout=args.timeout, retries=1,
                          proxy=os.environ.get("PROXY_URL") or None),
        only=only,
    ))
    out = write_snapshot(snapshot, Path(args.out))
    print(summarize(snapshot))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
