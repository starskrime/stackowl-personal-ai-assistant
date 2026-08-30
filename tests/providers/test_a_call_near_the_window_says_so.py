"""A call approaching the context window must announce itself.

WHY THIS EXISTS — a measurement with no shelf life silently expired.

D03.2 ("Conversation compression", P1, blocks D03.1 and D03.3) was CLOSED BY
MEASUREMENT on 2026-08-26 and the closure is recorded in progress.yml::

    2,916 production calls, median 1,487 / max 62,724 input tokens against a
    262,144 window, ZERO calls over 50%.

Re-run on 2026-08-30 against 42x more data, that closure is FALSIFIED::

    124,435 calls,  max single call 162,912 = 62.1% of the window
    calls over 50% of window : 7      (was ZERO)
    over 40% : 30    over 30% : 156    over 25% : 377

and the five largest calls are all dated 2026-08-28 — AFTER the measurement that
closed the item. The gap reopened and nothing said so, because **nothing anywhere
compares a call's size to the window.** The data existed in two places — the window
in the probe cache, the tokens in cost_records — and nobody joined them.

WHAT THIS IS AND IS NOT. It is the instrument, not the fix. No call has yet
exceeded the window and provider-side compression has NEVER fired (measured: zero
compression or oversize lines in the entire retained log), so building compression
today would be speculative. What was missing is the trigger that says WHEN it stops
being speculative.

This is CLAUDE.md's own rule turned on the programme's process: "If a log line is
the evidence for a claim, it must be INFO — and run the query that would close the
claim BEFORE you need it." A verdict of "closed by measurement" needs a standing
measurement, or it rots.

WARNING rather than INFO, deliberately: it fired 7 times in 124,435 calls, so it is
rare and actionable — it means the compression item is coming due.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.providers.base import _WINDOW_HIGH_WATER, window_pressure


def test_a_call_at_62_percent_of_the_window_is_reported() -> None:
    """The measured live maximum — 162,912 against a 262,144 window."""
    pressure = window_pressure(input_tokens=162_912, window=262_144)
    assert pressure is not None
    assert pressure == pytest.approx(0.62, abs=0.01)


def test_an_ordinary_call_is_silent() -> None:
    """Median is 5,248 tokens. The overwhelming majority must say nothing."""
    assert window_pressure(input_tokens=5_248, window=262_144) is None


def test_the_threshold_is_the_one_the_closure_used() -> None:
    """The stale closure asserted 'ZERO calls over 50%'. Watch that same line."""
    assert _WINDOW_HIGH_WATER == 0.5


def test_an_UNRESOLVED_window_is_silent_not_a_crash() -> None:
    """The window is probed; before it resolves, cached_window returns None.

    Cost accounting must NEVER break a completion that already happened, so an
    unknown window is silence, not an exception and not a false alarm.
    """
    assert window_pressure(input_tokens=999_999, window=None) is None
    assert window_pressure(input_tokens=999_999, window=0) is None


def test_it_fires_at_the_boundary_not_just_past_it() -> None:
    """'Over 50%' in the closure meant >=, so the watch must not miss the edge."""
    assert window_pressure(input_tokens=131_072, window=262_144) is not None


@pytest.mark.asyncio
async def test_the_provider_logs_it_at_WARNING(caplog: pytest.LogCaptureFixture) -> None:
    """Measured through the real recording path, not the pure helper.

    A helper that is never called from _record_cost would be exactly the
    built-but-not-wired defect this session has found six times.
    """
    from stackowl.providers import model_window
    from stackowl.providers.base import ModelProvider

    class _Probe(ModelProvider):
        """Minimal concrete provider — ModelProvider is abstract."""

        @property
        def name(self) -> str:
            return "probe-provider"

        @property
        def protocol(self):  # noqa: ANN201
            return "openai"

        async def complete(self, messages, model, **kwargs):  # noqa: ANN001,ANN003,ANN201
            raise NotImplementedError

        def stream(self, messages, model, **kwargs):  # noqa: ANN001,ANN003,ANN201
            raise NotImplementedError

    model_window._WINDOW_CACHE[("probe-provider", "probe-model")] = 262_144
    try:
        provider = _Probe.__new__(_Probe)
        provider._cost_tracker = None  # type: ignore[attr-defined]
        with caplog.at_level(logging.WARNING):
            await provider._record_cost(
                model="probe-model", input_tokens=200_000,
                output_tokens=10, duration_ms=1.0,
            )
        assert any(
            "window" in r.getMessage().lower() for r in caplog.records
        ), "a call at 76% of the window was recorded with no signal at all"
    finally:
        model_window._WINDOW_CACHE.pop(("probe-provider", "probe-model"), None)
