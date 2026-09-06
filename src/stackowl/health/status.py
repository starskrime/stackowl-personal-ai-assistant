"""HealthStatus dataclass and HealthContributor protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class HealthStatus:
    name: str
    status: Literal["ok", "degraded", "down"]
    message: str | None
    latency_ms: float
    #: WHAT TO DO ABOUT IT — the other half of a diagnosis (D14.4).
    #:
    #: `message` says what is wrong; this says what the operator can run or check. Until
    #: 2026-09-06 there was no field for it, so a contributor that KNEW the fix had
    #: nowhere to put it, and both surfaces an operator reads — `stackowl health` and the
    #: 2am alert — rendered "database not found: /path" and stopped, with the CLI then
    #: exiting 1.
    #:
    #: **None is a real answer and the common one.** It means no operator action is
    #: known, which is true of every measurement-style contributor: "the prompt prefix is
    #: growing" has no command that fixes it. Filling those in would be inventing advice,
    #: and a field that is always populated is a field readers learn to skim — the value
    #: is precisely that a remedy appearing means there IS something to do.
    #:
    #: Defaulted so all 72 existing construction sites are unaffected.
    remedy: str | None = None


class HealthContributor(Protocol):
    """Any subsystem that can report its own health."""

    @property
    def contributor_name(self) -> str: ...

    async def health_check(self) -> HealthStatus: ...
