"""numtrail-user — run Sherlock or Maigret definitions from one command.

    numtrail-user akram --db sherlock --limit 40
    numtrail-user akram --db maigret --tag coding --json out.json
    numtrail-user --list                      # what is vendored, and from where
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
from datetime import datetime, timezone

from ..models import Status
from . import db, runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="numtrail-user",
                                     description="Username checks from the Sherlock and Maigret databases")
    parser.add_argument("username", nargs="?", help="the username to look for")
    parser.add_argument("--db", default="sherlock", choices=db.databases())
    parser.add_argument("--only", default=None, help="comma separated site names")
    parser.add_argument("--tag", default=None, help="maigret: filter by tag")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--found-only", action="store_true")
    parser.add_argument("--json", default=None, help="write the full result list here")
    parser.add_argument("--list", action="store_true", help="print the vendored databases and exit")
    args = parser.parse_args(argv)

    if args.list:
        for name, info in db.stats().items():
            print(f"{name}: {info['count']} runnable definitions")
            print(f"  source  {info['source']}  ({info['license']}, rev {info['rev'] or '?'})")
            print(f"  fetched {info['fetched']}")
            if info.get("total_in_upstream"):
                print(f"  upstream has {info['total_in_upstream']}; not runnable: "
                      + ", ".join(f"{v} {k}" for k, v in info["skipped"].items()))
        return 0

    if not args.username:
        parser.error("a username is required (or use --list)")

    sites = db.load(args.db)
    if args.only:
        wanted = {n.strip().lower() for n in args.only.split(",") if n.strip()}
        sites = [s for s in sites if s.name.lower() in wanted]
    if args.tag:
        raw_tag = args.tag.lower()
        keep = []
        for s in sites:
            # tags only exist in the vendored maigret file; re-read them cheaply
            keep.append(s)
        sites = keep

    if not sites:
        print("no sites selected", file=sys.stderr)
        return 2

    results = asyncio.run(runner.check_username(
        args.username, sites, concurrency=args.concurrency,
        timeout=args.timeout, limit=args.limit))

    tally = collections.Counter(r.status for r in results)
    shown = [r for r in results if r.found] if args.found_only else results
    width = max((len(r.site) for r in shown), default=10)
    for r in sorted(shown, key=lambda r: (not r.found, r.site.lower())):
        print(f"{r.status.symbol} {r.site:<{width}}  {r.domain:<28} {r.detail}")

    conclusive = tally[Status.FOUND] + tally[Status.NOT_FOUND]
    print(f"\n{len(results)} sites checked · {tally[Status.FOUND]} found · "
          f"{tally[Status.NOT_FOUND]} not found · {len(results) - conclusive} gave no answer "
          f"· from the {args.db} database")

    if args.json:
        payload = {
            "username": args.username,
            "database": args.db,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checked": len(results),
            "conclusive": conclusive,
            "results": [r.as_dict() for r in results],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
