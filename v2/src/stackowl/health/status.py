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


class HealthContributor(Protocol):
    """Any subsystem that can report its own health."""

    @property
    def contributor_name(self) -> str: ...

    async def health_check(self) -> HealthStatus: ...
