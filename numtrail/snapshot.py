"""Build a results snapshot inside GitHub Actions.

This is the "backend on GitHub" path: GitHub Actions does the probing on a
schedule, and the results are committed back into the repository, where the
static Pages site reads them. No server, no third-party account, no token in a
browser.

Two ways in:

* the schedule (or a push touching the probes) re-probes ``watchlist.txt``;
* **Actions → snapshot → Run workflow** takes a number in a text box, so the
  GitHub UI itself is the query box: type a number, get a run, get a committed
  result the site can show.

The trade-off, stated plainly: the site still answers only for numbers that have
been probed here, not for arbitrary visitor input. Triggering a workflow from a web page needs a
token with `workflow` scope, and putting that in client-side JS would hand
anyone write access to your repo. A watchlist sidesteps that entirely — and it
is also the more responsible shape, because the site stays a viewer rather than
an open enumeration service.

    # scheduled / watchlist run
    python -m numtrail.snapshot --watchlist watchlist.txt \\
        --out numtrail/web/static/snapshots.json

    # what "Run workflow" does: probe one number, keep the watchlist too
    python -m numtrail.snapshot --number +60123456789 \\
        --out numtrail/web/static/snapshots.json

Keys/proxies come from the environment, so repository secrets work directly:
``NUMVERIFY_KEY``, ``VERIPHONE_KEY``, ``PROXY_URL`` …
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .engine import Settings, run_checks
from .models import Result, Status
from .phone import InvalidPhone, parse
from .registry import discover, select

__all__ = ["read_watchlist", "build_snapshot", "write_snapshot", "summarize",
           "merge_snapshot", "read_snapshot", "reconcile", "parse_numbers", "main"]

DEFAULT_KEEP_DISPATCH = 40

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
                         concurrency: int = 4,
                         source: str = "watchlist") -> dict:
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
            "source": source,
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
        "dispatch_size": 0,
        "credentials_present": sorted(k for k in WATCHLIST_KEYS if os.environ.get(k)),
        "proxy": bool(os.environ.get("PROXY_URL") or os.environ.get("HTTPS_PROXY")),
        "entries": entries,
        "skipped_lines": errors,
    }


def parse_numbers(raw: str) -> list[str]:
    """Split a Run-workflow text box into numbers.

    Accepts commas, semicolons, newlines, and the spaces people type between
    groups ("+60 12-345 6789").
    """
    out: list[str] = []
    for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
        piece = chunk.strip()
        if not piece:
            continue
        # keep a leading +, drop the rest of the punctuation people paste in
        out.append("+" + re.sub(r"[^0-9]", "", piece) if piece.startswith("+")
                   else re.sub(r"[^0-9]", "", piece))
    return [n for n in out if len(n) > 3]


def read_snapshot(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _entry_source(number: str, entry: dict, watchlist: Iterable[str]) -> str:
    """Entries written before `source` existed are assumed to be watchlist ones."""
    if not isinstance(entry, dict):      # hand-edited or foreign file
        return "watchlist"
    if entry.get("source"):
        return entry["source"]
    return "watchlist" if number in set(watchlist) else "dispatch"


def merge_snapshot(fresh: dict, previous: dict | None, *,
                   watchlist: Iterable[str] = (),
                   keep_dispatch: int = DEFAULT_KEEP_DISPATCH) -> dict:
    """Keep the previous snapshot's on-demand numbers, newest first, capped.

    A scheduled run must not erase what someone asked for with Run workflow —
    but the file also must not grow forever, so only the most recent
    `keep_dispatch` on-demand numbers survive.
    """
    entries = dict(fresh["entries"])
    carried: list[tuple[str, dict]] = []
    if previous:
        for number, entry in (previous.get("entries") or {}).items():
            if number in entries:
                continue                      # re-probed in this run
            if not isinstance(entry, dict):
                continue
            if _entry_source(number, entry, watchlist) != "dispatch":
                continue                      # dropped from watchlist.txt
            carried.append((number, {**entry, "source": "dispatch"}))

    carried.sort(key=lambda item: item[1].get("probed_at") or "", reverse=True)
    for number, entry in carried[:max(0, keep_dispatch)]:
        entries[number] = entry

    fresh["entries"] = entries
    fresh["dispatch_size"] = sum(1 for e in entries.values() if e.get("source") == "dispatch")
    fresh["watchlist_size"] = len(entries) - fresh["dispatch_size"]
    return fresh


def reconcile(fresh_path: Path, remote_path: Path, *,
              watchlist_path: Path | None = None,
              keep_dispatch: int = DEFAULT_KEEP_DISPATCH) -> dict:
    """Rebuild the snapshot after a rejected push.

    Two runs can probe at the same time (a push to the probes and a Run-workflow
    click, say). The push of the second is rejected, and a merge conflict on a
    generated file is meaningless — so keep this run's fresh results, carry over
    the other run's on-demand numbers, and write that.
    """
    fresh = read_snapshot(fresh_path)
    if fresh is None:
        raise SystemExit(f"no fresh snapshot at {fresh_path}")
    remote = read_snapshot(remote_path)
    watch: list[str] = []
    if watchlist_path and watchlist_path.is_file():
        watch = read_watchlist(watchlist_path)
    return merge_snapshot(fresh, remote, watchlist=watch,
                          keep_dispatch=keep_dispatch)


def write_snapshot(snapshot: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return path


def summarize(snapshot: dict) -> str:
    lines = [
        f"snapshot {snapshot.get('generated_at', '?')} · "
        f"{snapshot.get('watchlist_size', 0)} watchlisted · "
        f"{snapshot.get('dispatch_size', 0)} on demand · "
        f"credentials: {', '.join(snapshot.get('credentials_present') or []) or 'none'} · "
        f"proxy: {'yes' if snapshot.get('proxy') else 'no'}"
    ]
    for number, entry in (snapshot.get("entries") or {}).items():
        if not isinstance(entry, dict):
            continue
        tally: dict[str, int] = {}
        for r in entry.get("results") or []:
            tally[r["status"]] = tally.get(r["status"], 0) + 1
        compact = " ".join(f"{k}={v}" for k, v in sorted(tally.items()))
        tag = " (Run workflow)" if entry.get("source") == "dispatch" else ""
        lines.append(f"  {number:16} conclusive={entry.get('conclusive', 0):<2} {compact}{tag}")
        for domain in entry.get("found") or []:
            lines.append(f"      found: {domain}")
    for bad in snapshot.get("skipped_lines") or []:
        lines.append(f"  skipped line: {bad}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="numtrail.snapshot",
                                     description="Probe numbers and write a JSON snapshot")
    parser.add_argument("--watchlist", default="watchlist.txt",
                        help="one number per line (ignored with --skip-watchlist)")
    parser.add_argument("--number", action="append", default=None, metavar="NUM",
                        help="number to probe on demand; repeatable, and commas are "
                             "accepted inside one value (this is what the Run-workflow "
                             "text box passes in)")
    parser.add_argument("--skip-watchlist", action="store_true",
                        help="probe only --number values, for a fast run")
    parser.add_argument("--keep-dispatch", type=int, default=DEFAULT_KEEP_DISPATCH,
                        help="how many earlier on-demand numbers to carry over")
    parser.add_argument("--out", default="numtrail/web/static/snapshots.json")
    parser.add_argument("--merge", action="store_true",
                        help="rebuild --out from --fresh after a rejected push, "
                             "carrying over the numbers in --remote")
    parser.add_argument("--fresh", default=None, help="this run's own results")
    parser.add_argument("--remote", default=None, help="the other run's committed file")
    parser.add_argument("--modules", default=None, help="comma separated subset")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.merge:
        if not args.fresh:
            print("--merge needs --fresh")
            return 2
        merged = reconcile(Path(args.fresh),
                           Path(args.remote) if args.remote else Path(args.out),
                           watchlist_path=Path(args.watchlist),
                           keep_dispatch=args.keep_dispatch)
        path = write_snapshot(merged, Path(args.out))
        print(summarize(merged))
        print(f"\nre-merged into {path}")
        return 0

    watchlist_path = Path(args.watchlist)
    watch_numbers: list[str] = []
    if not args.skip_watchlist:
        if not watchlist_path.is_file():
            print(f"no watchlist at {watchlist_path} — create one, or pass --number")
            return 2
        watch_numbers = read_watchlist(watchlist_path)

    dispatch_numbers: list[str] = []
    for raw in args.number or []:
        dispatch_numbers.extend(parse_numbers(raw))

    if not watch_numbers and not dispatch_numbers:
        print("nothing to probe: the watchlist is empty and no --number was given")
        return 2

    only = [m.strip() for m in args.modules.split(",")] if args.modules else None
    settings = Settings(timeout=args.timeout, retries=1,
                        proxy=os.environ.get("PROXY_URL") or None)
    out = Path(args.out)
    previous = read_snapshot(out)

    snapshot: dict | None = None
    for numbers, source in ((watch_numbers, "watchlist"), (dispatch_numbers, "dispatch")):
        if not numbers:
            continue
        built = asyncio.run(build_snapshot(numbers, settings=settings, only=only,
                                           source=source))
        if snapshot is None:
            snapshot = built
        else:
            snapshot["generated_at"] = built["generated_at"]
            snapshot["entries"].update(built["entries"])
            snapshot["skipped_lines"].extend(built["skipped_lines"])
            snapshot["credentials_present"] = built["credentials_present"]
            snapshot["proxy"] = built["proxy"]

    assert snapshot is not None
    snapshot = merge_snapshot(snapshot, previous, watchlist=watch_numbers,
                              keep_dispatch=args.keep_dispatch)
    path = write_snapshot(snapshot, out)
    print(summarize(snapshot))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
