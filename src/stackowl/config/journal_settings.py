"""JournalSettings -- the journal's own tunables (AD-6, NFR44, Story 2.11).

AD-6: "Journal retention is a setting, pruned by one seeded job... Retention
is a setting with a 30-day default." A constant compiled into
``journal/retention.py`` cannot be changed without a deploy, so the real
value lives here and that module derives its own tripwire-only constant from
this settings model's default (see ``journal/retention.py``'s docstring for
why the derivation runs one way and not the other).

Its own file rather than another block inside ``settings.py`` (already
~1,000 lines) -- same shape as ``task_loop_settings.py`` and
``notification_settings.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class JournalSettings(BaseModel):
    """How long the journal keeps events, and how much WAL it tolerates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    retention_days: int = Field(
        default=30,
        ge=1,
        description=(
            "How long a journal event survives before the seeded journal_prune "
            "job deletes it (AD-6, NFR44). The 30-day architecture default -- "
            "provisional until Story 2.12's benchmark reports real measurements "
            "from the platform's own hardware."
        ),
    )
    wal_size_budget_bytes: int = Field(
        default=67_108_864,
        ge=1,
        description=(
            "The journal's WAL file size the health contributor tolerates "
            "before degrading (64 MiB). PROVISIONAL pending Story 2.12's "
            "benchmark, mirrors turn_budget.py's PROVISIONAL_* precedent: a "
            "round, deliberately generous ceiling rather than a measured "
            "number."
        ),
    )
