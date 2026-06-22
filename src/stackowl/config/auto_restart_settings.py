"""AutoRestartSettings — dev hot-restart of the core process on code change.

When the gateway/core process split is active (``runtime.split_process``), this
block drives the development convenience of auto-restarting *only the core*
process when source code under ``watch_paths`` changes — so edits take effect
without losing the terminal UI / open chat (the durable gateway never restarts).

Defaults OFF: the baseline runtime never auto-restarts. Timing is a SETTLE /
quiet-period debounce — the restart fires only after ``delay_minutes`` of no
further change — and it waits for any in-flight turn to drain (up to
``grace_seconds``) before exec-replacing the core. Mirrors the existing
``ConfigWatcher`` (``config/watcher.py``): daemon-thread mtime polling with a
settle window.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AutoRestartSettings(BaseModel):
    """Code-change auto-restart configuration for the core process."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch for auto-restarting the core process when watched "
            "source files change. OFF by default (baseline never auto-restarts). "
            "Only takes effect when runtime.split_process is also true."
        ),
        json_schema_extra={"hot_reload": True},
    )
    delay_minutes: float = Field(
        default=5.0,
        ge=0,
        description=(
            "Settle / quiet-period before a detected code change triggers a "
            "restart: the restart fires only after this many minutes with NO "
            "further change (debounce). A burst of edits coalesces into one "
            "restart. 0 = restart as soon as the change settles for one poll."
        ),
        json_schema_extra={"hot_reload": True},
    )
    watch_paths: list[str] = Field(
        default_factory=lambda: ["src"],
        min_length=1,
        description=(
            "Repo-relative paths whose *.py mtimes are polled for changes. "
            "Defaults to the source tree."
        ),
        json_schema_extra={"hot_reload": True},
    )
    require_client_connected: bool = Field(
        default=True,
        description=(
            "Only restart while a client (TUI/channel) is connected to the "
            "gateway. Avoids churn when nothing is attached to observe it."
        ),
        json_schema_extra={"hot_reload": True},
    )
    grace_seconds: float = Field(
        default=120.0,
        ge=0,
        description=(
            "Max seconds to wait for an in-flight turn to finish before forcing "
            "the restart. Stragglers are durable-checkpointed or terminally "
            "stopped at the ceiling."
        ),
        json_schema_extra={"hot_reload": True},
    )
    poll_interval_s: float = Field(
        default=2.0,
        gt=0,
        description="How often (seconds) the code watcher polls watched mtimes.",
        json_schema_extra={"hot_reload": True},
    )
