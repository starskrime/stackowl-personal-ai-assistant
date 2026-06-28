"""TriggerSpec — how a `lifecycle="scheduled"` owl is woken.

A discriminated union on ``kind`` so each trigger type carries exactly its own
fields and nothing else. The owl manifest holds the DECLARATION; the lifecycle
reconcile loop (ADR-B) projects it into a scheduler row, and the matching handler
(GoalExecutionHandler / WebsiteWatchHandler / ThresholdWatchHandler) runs it.

Kept deliberately small and vendor-neutral: a ``threshold`` source is a generic
numeric source string (a tool name or URL+extractor), never a market/data SDK —
the owl already has web_fetch. See [[UNIOWL_ARCHITECTURE.md]] ADR-B/ADR-C.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _TriggerBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CronTrigger(_TriggerBase):
    """Run an NL goal on a recurring schedule (cron / ``every Nm`` / ``daily@HH:MM``)."""

    kind: Literal["cron"] = "cron"
    schedule: str
    prompt: str  # the natural-language goal executed each tick


class WatchTrigger(_TriggerBase):
    """Watch a URL or filesystem path; act only when its content CHANGES."""

    kind: Literal["watch"] = "watch"
    target: str  # url or path
    schedule: str = "every 15m"
    prompt: str = ""  # optional NL instruction applied on change


class ThresholdTrigger(_TriggerBase):
    """Poll a numeric source; fire only when a predicate crosses (edge-triggered).

    ``source`` is a generic numeric source (tool name or URL+extractor) — NO
    vendor/market SDK. Hysteresis (fire once on cross, re-arm after crossing back)
    lives in the handler, not here.
    """

    kind: Literal["threshold"] = "threshold"
    source: str
    op: Literal["gt", "lt", "ge", "le", "eq"]
    threshold: float
    schedule: str = "every 5m"
    prompt: str = ""  # optional NL instruction applied when the predicate fires


TriggerSpec = Annotated[
    CronTrigger | WatchTrigger | ThresholdTrigger,
    Field(discriminator="kind"),
]
