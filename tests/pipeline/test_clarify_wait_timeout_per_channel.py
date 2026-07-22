"""STEER-7 (F094) — the budget clarify wait timeout scales per channel.

F094: the budget gate blocked up to a hardcoded 120s awaiting a human Raise/Stop.
A slow-typing Telegram user could be auto-Stopped before answering. The fix: the
wait timeout is sourced from settings PER CHANNEL, with 120s kept as the fallback
default. A fast terminal (CLI) can keep a short wait; a mobile channel (telegram)
can be given a generous one — both from config, no hardcoded per-case value.
"""

from __future__ import annotations

from stackowl.config.settings import ClarifySettings
from stackowl.pipeline.budget.callback import resolve_clarify_wait_timeout


def test_default_is_600s_when_unconfigured() -> None:
    """Raised 120.0 -> 600.0 on 2026-07-22 (owner decision)."""
    s = ClarifySettings()
    assert resolve_clarify_wait_timeout("cli", s) == 600.0
    assert resolve_clarify_wait_timeout("telegram", s) == 600.0
    assert s.wait_timeout_s == 600.0  # the documented fallback default


def test_global_override_applies_to_all_channels() -> None:
    s = ClarifySettings(wait_timeout_s=300.0)
    assert resolve_clarify_wait_timeout("cli", s) == 300.0
    assert resolve_clarify_wait_timeout("telegram", s) == 300.0


def test_per_channel_override_wins_over_global_default() -> None:
    s = ClarifySettings(wait_timeout_s=120.0, per_channel={"telegram": 600.0})
    # A slow-Telegram user gets the generous per-channel wait...
    assert resolve_clarify_wait_timeout("telegram", s) == 600.0
    # ...while other channels fall back to the global default.
    assert resolve_clarify_wait_timeout("cli", s) == 120.0


def test_unknown_channel_falls_back_to_global_default() -> None:
    s = ClarifySettings(wait_timeout_s=90.0, per_channel={"telegram": 600.0})
    assert resolve_clarify_wait_timeout("some_future_channel", s) == 90.0


def test_resolver_is_fail_safe_on_bad_input() -> None:
    # A None settings or odd channel must never raise — fall back to 600s.
    assert resolve_clarify_wait_timeout("cli", None) == 600.0
