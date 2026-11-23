"""Result types shared by every module and renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """What a module managed to learn.

    The vocabulary is deliberately larger than a boolean: most of the time a
    check neither confirms nor denies anything, it just gets stonewalled. Those
    outcomes are reported honestly instead of being flattened into "not found".
    """

    FOUND = "found"                      # the module's question answered yes
    NOT_FOUND = "not_found"              # answered no
    RATE_LIMITED = "rate_limited"        # 429 / quota exhausted -> retry or rotate
    BLOCKED = "blocked"                  # captcha, WAF, login wall, migrated endpoint
    NEEDS_CREDENTIALS = "needs_credentials"  # module needs an API key/env var
    ERROR = "error"                      # unexpected: bad JSON, timeout, DNS
    SKIPPED = "skipped"                  # not run (deselected, or prerequisite missing)

    @property
    def conclusive(self) -> bool:
        return self in (Status.FOUND, Status.NOT_FOUND)

    @property
    def retryable(self) -> bool:
        return self in (Status.RATE_LIMITED, Status.ERROR)

    @property
    def symbol(self) -> str:
        return {
            Status.FOUND: "+",
            Status.NOT_FOUND: "-",
            Status.RATE_LIMITED: "x",
            Status.BLOCKED: "#",
            Status.NEEDS_CREDENTIALS: "k",
            Status.ERROR: "!",
            Status.SKIPPED: ".",
        }[self]

    @property
    def style(self) -> str:
        return {
            Status.FOUND: "green",
            Status.NOT_FOUND: "magenta",
            Status.RATE_LIMITED: "red",
            Status.BLOCKED: "yellow",
            Status.NEEDS_CREDENTIALS: "cyan",
            Status.ERROR: "red",
            Status.SKIPPED: "bright_black",
        }[self]


@dataclass(slots=True)
class Result:
    """One module's outcome for one phone number."""

    module: str
    domain: str
    status: Status
    method: str = "other"
    semantic: str = "account_exists"   # or "number_valid"
    tier: str = "live"                 # live | legacy | api
    detail: str | None = None
    elapsed: float = 0.0
    attempts: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.status is Status.FOUND

    @property
    def conclusive(self) -> bool:
        return self.status.conclusive

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "module": self.module,
            "domain": self.domain,
            "status": self.status.value,
            "found": self.found,
            "method": self.method,
            "semantic": self.semantic,
            "tier": self.tier,
            "detail": self.detail,
            "elapsed": round(self.elapsed, 3),
            "attempts": self.attempts,
        }
        if self.extra:
            out.update(self.extra)
        return out
