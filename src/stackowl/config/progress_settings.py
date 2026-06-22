"""ProgressSettings — live turn-progress UX configuration block.

Live progress surfaces "Working on it… / Searching the web… / Writing your
answer…" while a turn runs, in both Telegram and the terminal TUI, then settles
into the final answer.

Unlike most augmentation flags in this repo (which default OFF for a byte-identical
baseline), ``live_progress`` defaults **ON**: silence during a 30-40s turn is the
exact problem this feature fixes, so the desired baseline IS progress-visible.
Operators who want the old all-at-once behaviour set ``live_progress: false``.
A per-user ``progress_mode`` preference can still suppress it per recipient.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProgressSettings(BaseModel):
    """Live turn-progress configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    live_progress: bool = Field(
        default=True,
        description=(
            "Master switch for live turn-progress UX (typing indicator + a "
            "mutating status line on Telegram, and the animated PipelineStrip in "
            "the terminal). ON by default — fixes the 'silent void' problem. "
            "Set false to restore the all-at-once baseline."
        ),
        json_schema_extra={"hot_reload": True},
    )
    telegram_edit_min_interval_s: float = Field(
        default=1.0,
        description=(
            "Minimum seconds between Telegram status-message edits. Coalesces "
            "rapid state changes (keeps the latest) to stay under Telegram's "
            "~1 edit/sec rate limit."
        ),
        json_schema_extra={"hot_reload": True},
    )
    typing_reissue_interval_s: float = Field(
        default=4.0,
        description=(
            "Seconds between re-issuing the Telegram 'typing' chat action "
            "(Telegram clears it after ~5s, so it must be refreshed)."
        ),
        json_schema_extra={"hot_reload": True},
    )
    flicker_guard_ms: float = Field(
        default=400.0,
        description=(
            "Suppress any progress status for the first N milliseconds of a turn "
            "so fast turns (<400ms) never flash a status that instantly vanishes."
        ),
        json_schema_extra={"hot_reload": True},
    )
    tick_interval_s: float = Field(
        default=3.0,
        description=(
            "How often the background liveness ticker wakes to refresh the Telegram "
            "status during a long model 'think' with no ReAct events — re-issues "
            "typing and updates the elapsed counter so the chat never looks dead."
        ),
        json_schema_extra={"hot_reload": True},
    )
    elapsed_after_s: float = Field(
        default=10.0,
        description=(
            "Only append an elapsed counter ('Working on it… (12s)') once the turn "
            "has run this many seconds — a ticking counter on a short turn is noise."
        ),
        json_schema_extra={"hot_reload": True},
    )
    reassure_after_s: float = Field(
        default=30.0,
        description=(
            "After this many seconds, a still-generic status ('Working on it…') "
            "switches to a reassurance phrase ('Still working on this…')."
        ),
        json_schema_extra={"hot_reload": True},
    )
