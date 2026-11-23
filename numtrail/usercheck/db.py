"""Sherlock and Maigret definitions, vendored and made runnable.

Both projects answer one question: *does this username exist on this site?* They
ship a database of ~480 (Sherlock) and ~11,000 (Maigret) entries, each saying how
to build the URL and how to tell "exists" from "does not".

Two things to be straight about:

* These are **username** checks. A phone number is not a username; feeding
  ``+60123456789`` into ``https://example.com/{}`` asks a different question and
  the answer would be meaningless. numtrail keeps the two backends separate:
  ``numtrail`` (phone, ignorant-style probes) and ``numtrail-user`` (this one).
* Maigret's 11,071 sites are not 11,071 runnable definitions. 7,747 have no
  ``{username}`` placeholder (they are engine lookups or APIs) and 46 need a
  per-site engine to get past anti-bot. 3,268 can be run as plain requests,
  which is what is vendored here.

Data is vendored, gzipped, with upstream commit and licence, so tests never hit
the network and the numbers are reproducible:

    numtrail/usercheck/data/sherlock.json.gz   MIT, 478 sites
    numtrail/usercheck/data/maigret.json.gz    MIT, 3,268 of 11,071
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

__all__ = ["SiteDef", "load", "databases", "stats"]

DATA = Path(__file__).with_name("data")
DATABASES = {"sherlock": "sherlock.json.gz", "maigret": "maigret.json.gz"}


@dataclass(frozen=True, slots=True)
class SiteDef:
    """One site's definition, normalised across both projects."""

    name: str
    url: str                      # with a literal {} where the username goes
    domain: str
    check: str                    # status_code | message | response_url
    method: str = "GET"
    error_msg: tuple[str, ...] = ()       # sherlock: strings that mean "absent"
    presence: tuple[str, ...] = ()        # maigret: strings that mean "present"
    absence: tuple[str, ...] = ()         # maigret: strings that mean "absent"
    regex: str | None = None              # sherlock: valid-username pattern
    payload: str | None = None
    headers: dict | None = None
    source: str = "sherlock"

    def url_for(self, username: str) -> str:
        return self.url.replace("{}", username)

    def body_for(self, username: str) -> str:
        """POST payload with the username substituted.

        Upstream stores this either as a JSON string or as an object, so both
        shapes are accepted instead of exploding on one of them.
        """
        payload = self.payload if self.payload is not None else "{}"
        if not isinstance(payload, str):
            payload = json.dumps(payload)
        return payload.replace("{}", username)


def databases() -> list[str]:
    return sorted(DATABASES)


def _raw(name: str) -> dict:
    path = DATA / DATABASES[name]
    return json.loads(gzip.decompress(path.read_bytes()))


def stats() -> dict[str, dict]:
    """What is vendored, and where it came from — surfaced in ``--list``."""
    out = {}
    for name in databases():
        raw = _raw(name)
        out[name] = {
            "count": raw["count"],
            "source": raw["source"],
            "license": raw["license"],
            "rev": raw.get("rev", ""),
            "fetched": raw.get("fetched", ""),
        }
        if raw.get("total_in_upstream"):
            out[name]["total_in_upstream"] = raw["total_in_upstream"]
            out[name]["skipped"] = raw.get("skipped", {})
    return out


def load(name: str = "sherlock") -> list[SiteDef]:
    """Every runnable definition in one of the vendored databases."""
    if name not in DATABASES:
        raise KeyError(f"unknown database {name!r}; try {databases()}")
    raw = _raw(name)
    sites: list[SiteDef] = []
    for site_name, v in raw["sites"].items():
        url = v["url"]
        if not url.startswith(("http://", "https://")):
            # a handful of upstream templates are broken ("/user.ddns.net");
            # they cannot be requested at all, so they are not runnable
            continue
        domain = (v.get("urlMain") or url).split("://")[-1].split("/")[0]
        if name == "sherlock":
            sites.append(SiteDef(
                name=site_name, url=url, domain=domain,
                check=v.get("errorType") or "status_code",
                method=(v.get("method") or "GET").upper(),
                error_msg=tuple(v.get("errorMsg") or ()),
                regex=v.get("regexCheck"),
                payload=v.get("payload"),
                headers=v.get("headers"),
                source="sherlock",
            ))
        else:
            sites.append(SiteDef(
                name=site_name, url=url, domain=domain,
                check=v.get("checkType") or "status_code",
                method=(v.get("method") or "GET").upper(),
                presence=tuple(v.get("presenseStrs") or ()),
                absence=tuple(v.get("absenseStrs") or ()),
                headers=v.get("headers"),
                source="maigret",
            ))
    return sites
