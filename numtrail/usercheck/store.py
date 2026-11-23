"""Keep several usernames in one committed file.

The first version of ``usernames.json`` held exactly one run, so looking up any
other username on the static site said "run the workflow again". That is a
worse answer than the page could give: every earlier run is still in git, and a
workflow dispatch takes a minute.

So the file is now a small store:

    {
      "generator": "numtrail.usercheck",
      "generated_at": "2026-11-23T09:30:01Z",      # newest run
      "latest": "sakib112",
      "usernames": {
        "torvalds": {"database": "sherlock", "generated_at": "...",
                     "checked": 478, "conclusive": 253, "results": [ ... ]},
        "sakib112": { ... }
      }
    }

Bounded on purpose: ``keep`` runs (default 8) and ``keep_bytes`` (default 4 MB)
of results, oldest dropped first. A Maigret run is ~700 KB, so a couple of those
fill the budget and the sherlock runs age out — the file in git stays small and
the page stays quick.

The old single-run shape is read as one entry, so an existing commit migrates
instead of breaking.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["DEFAULT_KEEP", "DEFAULT_KEEP_BYTES", "empty", "load", "merge",
           "runs", "get", "dumps"]

DEFAULT_KEEP = 8
DEFAULT_KEEP_BYTES = 4_000_000


def empty() -> dict:
    return {"generator": "numtrail.usercheck", "generated_at": None,
            "latest": None, "usernames": {}}


def load(path: Path) -> dict:
    """Read the store, migrating the old one-run-per-file shape."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty()
    if not isinstance(raw, dict):
        return empty()

    if "usernames" in raw and isinstance(raw["usernames"], dict):
        store = empty()
        store.update({k: raw.get(k) for k in ("generated_at", "latest")})
        store["usernames"] = {k: v for k, v in raw["usernames"].items()
                              if isinstance(v, dict)}
        return store

    # v1: {"username": ..., "database": ..., "checked": ..., "results": [...]}
    if raw.get("username") and isinstance(raw.get("results"), list):
        store = empty()
        name = str(raw["username"])
        store["usernames"][name] = {
            "database": raw.get("database"),
            "generated_at": raw.get("generated_at"),
            "checked": raw.get("checked", len(raw["results"])),
            "conclusive": raw.get("conclusive"),
            "results": raw["results"],
        }
        store["latest"] = name
        store["generated_at"] = raw.get("generated_at")
        return store
    return empty()


def runs(store: dict) -> dict:
    return store.get("usernames") or {}


def get(store: dict, username: str) -> dict | None:
    return runs(store).get(str(username).lstrip("@").strip())


def merge(store: dict, username: str, payload: dict, *,
          keep: int = DEFAULT_KEEP,
          keep_bytes: int = DEFAULT_KEEP_BYTES) -> dict:
    """Add or refresh one username, then prune to stay small.

    ``payload`` is a single run: database, generated_at, checked, conclusive,
    results. Re-running a username replaces its entry rather than duplicating it.
    """
    username = str(username).lstrip("@").strip()
    entry = {
        "database": payload.get("database"),
        "generated_at": payload.get("generated_at"),
        "checked": payload.get("checked", len(payload.get("results") or [])),
        "conclusive": payload.get("conclusive"),
        "results": payload.get("results") or [],
    }
    merged = {**runs(store), username: entry}

    # newest first, by the run's own timestamp (not insertion order)
    ordered = sorted(merged.items(),
                     key=lambda kv: kv[1].get("generated_at") or "", reverse=True)
    kept: dict[str, dict] = {}
    budget = keep_bytes
    for name, value in ordered[:max(1, keep)]:
        size = len(json.dumps({name: value}, separators=(",", ":")).encode())
        if kept and size > budget:
            continue                    # too big for what is left, try a smaller one
        kept[name] = value
        budget -= size

    out = empty()
    out["usernames"] = kept
    out["latest"] = username if username in kept else next(iter(kept), None)
    stamps = [v.get("generated_at") for v in kept.values() if v.get("generated_at")]
    out["generated_at"] = max(stamps) if stamps else payload.get("generated_at")
    return out


def dumps(store: dict, *, indent: int | None = 1) -> str:
    return json.dumps(store, indent=indent, ensure_ascii=False) + "\n"
