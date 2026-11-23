"""Renderers: a human table, JSON, CSV, and a bare list of found domains."""

from __future__ import annotations

import csv
import io
import json
from typing import Iterable, Sequence

from .models import Result, Status
from .phone import Phone

LEGEND = "[+] found   [-] not registered   [x] rate limited   [#] blocked   [k] needs key   [!] error   [.] skipped"

TIER_NOTE = {
    "live": "",
    "legacy": "public endpoint, prone to bot-walls",
    "api": "keyed API, reports number validity not account existence",
}


def _use_color(no_color: bool) -> bool:
    return not no_color


def render_table(results: Sequence[Result], phone: Phone, elapsed: float,
                 *, no_color: bool = False, only_found: bool = False,
                 show_detail: bool = True) -> str:
    rows = [r for r in results if r.found or not only_found]
    lines: list[str] = []

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console(color_system=None if no_color else "auto", width=100,
                          highlight=False, soft_wrap=False)
        table = Table(box=None, pad_edge=False, show_edge=False, header_style="bold")
        table.add_column("", width=2, no_wrap=True)
        table.add_column("module", style="bold", no_wrap=True)
        table.add_column("domain", no_wrap=True)
        table.add_column("status", no_wrap=True)
        table.add_column("detail", overflow="fold")
        for r in rows:
            table.add_row(
                r.status.symbol,
                r.module,
                r.domain,
                r.status.value.replace("_", " "),
                r.detail or "",
                style=r.status.style if _use_color(no_color) else None,
            )
        with console.capture() as cap:
            console.print(table)
        lines.append(cap.get().rstrip("\n"))
    except Exception:  # pragma: no cover - rich missing or unhappy
        for r in rows:
            detail = f"  {r.detail}" if (show_detail and r.detail) else ""
            lines.append(f"[{r.status.symbol}] {r.module:<14} {r.domain:<22} "
                         f"{r.status.value:<18}{detail}")

    found = [r for r in results if r.found]
    totals = _tally(results)
    header = f"{phone.pretty()}  ({phone.iso or '??'})"
    summary = (
        f"{len(found)}/{len(results)} yes · "
        f"{totals.get(Status.NOT_FOUND, 0)} no · "
        f"{totals.get(Status.RATE_LIMITED, 0)} limited · "
        f"{totals.get(Status.BLOCKED, 0)} blocked · "
        f"{totals.get(Status.ERROR, 0)} error · "
        f"{totals.get(Status.NEEDS_CREDENTIALS, 0)} no key"
    )
    out = [header, "-" * max(len(header), 48), *lines, "", summary,
           f"finished in {elapsed:.2f}s", LEGEND]
    return "\n".join(out)


def _tally(results: Iterable[Result]) -> dict[Status, int]:
    counts: dict[Status, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def to_json(results: Sequence[Result], phone: Phone, elapsed: float) -> str:
    payload = {
        "phone": phone.e164,
        "dial": phone.dial,
        "national": phone.national,
        "iso": phone.iso,
        "elapsed": round(elapsed, 3),
        "results": [r.as_dict() for r in results],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def to_csv(results: Sequence[Result]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["module", "domain", "status", "found", "method",
                     "semantic", "tier", "detail", "elapsed"])
    for r in results:
        writer.writerow([r.module, r.domain, r.status.value, int(r.found),
                         r.method, r.semantic, r.tier, r.detail or "",
                         f"{r.elapsed:.3f}"])
    return buf.getvalue()


def found_domains(results: Sequence[Result]) -> list[str]:
    return [r.domain for r in results if r.found]
