"""Command line interface: ``numtrail [options] NUMBER...``"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Sequence

from . import __version__
from .engine import Settings, run_checks
from .models import Status
from .output import found_domains, render_table, to_csv, to_json
from .phone import InvalidPhone, parse
from .registry import select

DESCRIPTION = """\
Trace where a phone number is registered across sites and APIs.

Accepts a single E.164-ish number (numtrail +60123456789) or a dialling
code plus national number (numtrail 60 123456789).
"""

EPILOG = """\
examples:
  numtrail +60123456789
  numtrail 60 123456789 --only-found
  numtrail +15550100 -m twilio,numverify --json
  numtrail +60123456789 --tags social --concurrency 4 --timeout 8
  numtrail --list-modules
  numtrail +60123456789 --proxy http://user:pass@host:8080   # rotate when limited
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="numtrail", description=DESCRIPTION, epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("numbers", nargs="*", metavar="NUMBER",
                   help="+60 123456789 / 60123456789 / (dial code, national number)")
    p.add_argument("-m", "--modules", default=None, metavar="LIST",
                   help="comma separated modules to run (default: all)")
    p.add_argument("-x", "--exclude", default=None, metavar="LIST",
                   help="comma separated modules to skip")
    p.add_argument("--tags", default=None, metavar="LIST",
                   help="only run modules carrying any of these tags")
    p.add_argument("--only-found", action="store_true",
                   help="show only modules that answered yes")
    p.add_argument("--json", action="store_true", help="machine readable output")
    p.add_argument("--csv", metavar="PATH", help="also write CSV to PATH")
    p.add_argument("--concurrency", type=int, default=8, metavar="N",
                   help="max modules in flight (default 8)")
    p.add_argument("--timeout", type=float, default=15.0, metavar="SEC",
                   help="per module timeout (default 15)")
    p.add_argument("--retries", type=int, default=1, metavar="N",
                   help="retries for rate limits / errors (default 1)")
    p.add_argument("--proxy", default=None, metavar="URL",
                   help="HTTP(S)/SOCKS proxy for all modules")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    p.add_argument("--list-modules", action="store_true",
                   help="show the module table and exit")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="suppress the summary, print found domains only")
    p.add_argument("-V", "--version", action="version",
                   version=f"numtrail {__version__}")
    return p


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _resolve_numbers(args: list[str]) -> list[str]:
    """Support both ``+60123456789`` and ``60 123456789``."""
    if len(args) == 2 and args[0].isdigit() and len(args[0]) <= 3 and args[1].isdigit():
        return [args[0] + args[1]]
    return args


def _print_modules() -> int:
    specs = select()
    width = max(len(s.name) for s in specs) + 2
    print(f"{len(specs)} modules\n")
    print(f"{'name'.ljust(width)}{'tier'.ljust(8)}{'semantic'.ljust(16)}{'domain'.ljust(24)}requires")
    for s in sorted(specs, key=lambda s: (s.tier, s.name)):
        requires = ",".join(s.requires) or "-"
        print(f"{s.name.ljust(width)}{s.tier.ljust(8)}{s.semantic.ljust(16)}"
              f"{s.domain.ljust(24)}{requires}")
    print("\ntiers: live = works from a normal IP · legacy = public endpoint behind "
          "bot-walls, best effort · api = needs a key, answers 'is this number valid'")
    return 0


async def _run(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv))

    if args.list_modules:
        return _print_modules()

    raw_numbers = _resolve_numbers(list(args.numbers))
    if not raw_numbers:
        parser.error("no phone number given (try: numtrail +60123456789)")

    phones = []
    for raw in raw_numbers:
        try:
            phones.append(parse(raw))
        except InvalidPhone as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    settings = Settings(timeout=args.timeout, retries=args.retries,
                        concurrency=args.concurrency, proxy=args.proxy)
    only, exclude, tags = _split(args.modules), _split(args.exclude), _split(args.tags)

    try:
        select(only=only, exclude=exclude, tags=tags)
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2

    exit_code = 0
    for index, phone in enumerate(phones):
        started = time.perf_counter()
        results = await run_checks(phone, settings=settings, only=only,
                                   exclude=exclude, tags=tags)
        elapsed = time.perf_counter() - started

        if args.json:
            print(to_json(results, phone, elapsed))
        elif args.quiet:
            for domain in found_domains(results):
                print(domain)
        else:
            print(render_table(results, phone, elapsed, no_color=args.no_color,
                               only_found=args.only_found))
        if args.csv:
            path = Path(args.csv)
            if len(phones) > 1:
                path = path.with_name(f"{path.stem}-{phone.dial}{phone.national}{path.suffix}")
            path.write_text(to_csv(results), encoding="utf-8")
            if not args.json and not args.quiet:
                print(f"csv written to {path}")
        if index < len(phones) - 1 and not (args.json or args.quiet):
            print()

    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    import asyncio

    return asyncio.run(_run(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
