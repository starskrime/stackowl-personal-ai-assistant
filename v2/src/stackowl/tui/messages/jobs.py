"""Job-scheduler messages."""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.tui.messages._base import FrozenMessage


@dataclass(frozen=True)
class JobPausedMessage(FrozenMessage):
    """Emitted when a scheduled job is paused due to repeated failures."""

    job_id: str
    handler: str
    last_error: str


@dataclass(frozen=True)
class BudgetAlertMessage(FrozenMessage):
    """Emitted when the daily-budget consumption crosses a threshold."""

    pct: float
    cost_today: float
