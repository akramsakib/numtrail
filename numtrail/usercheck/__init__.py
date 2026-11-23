"""Sherlock and Maigret, ported so their definition databases actually run.

    from numtrail.usercheck import db, runner
    sites = db.load("sherlock")                     # 478 runnable definitions
    results = await runner.check_username("akram", sites, limit=20)

This is the username half of the backend. The phone half lives in
``numtrail.modules`` (the ignorant-style probes); the two are deliberately not
mixed, because "does user @x exist" and "is +60123456789 registered" are
different questions.
"""

from __future__ import annotations

from .db import SiteDef, databases, load, stats
from .runner import UserResult, check_site, check_username

__all__ = ["SiteDef", "databases", "load", "stats",
           "UserResult", "check_site", "check_username"]
