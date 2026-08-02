"""D05.2 — per-owl tool-usage scoring, the replacement ordering signal.

The signal is read from ``task_outcomes``, which has carried
``(owl_name, tool_sequence)`` on every turn since 2026-05-28. These tests pin the
three things that make that read correct rather than merely plausible: the
positives-only gate is the SHARED one, the recency weighting actually decays, and
every failure path returns an empty mapping rather than raising into a turn.
"""

from __future__ import annotations

import pytest

from stackowl.memory.outcome_store import TaskOutcome
from stackowl.tools._infra.tool_usage import (
    DEMOTION_HALF_LIFE_DAYS,
    score_tools_for_owl,
)

_DAY = 86_400.0
_NOW = 1_800_000_000.0


def _outcome(
    *,
    tools: list[str],
    age_days: float = 0.0,
    success: bool = True,
    failure_class: str | None = None,
    approach_rating: str | None = None,
    quality: float | None = 0.9,
) -> TaskOutcome:
    return TaskOutcome(
        outcome_id="o", trace_id="t", session_key="s", owl_name="scout",
        channel="cli", success=success, latency_ms=1.0, tool_call_count=len(tools),
        failure_class=failure_class, quality_score=quality,
        step_durations={}, input_text="", response_text="",
        captured_at=_NOW - age_days * _DAY, scored_at=None,
        tool_sequence=tuple(tools), approach_rating=approach_rating,
    )


class _Store:
    """Stands in for TaskOutcomeStore.list_tool_usage_for_owl."""

    def __init__(self, rows: list[TaskOutcome], *, boom: bool = False) -> None:
        self._rows, self._boom = rows, boom
        self.calls: list[dict] = []

    async def list_tool_usage_for_owl(self, **kwargs):
        self.calls.append(kwargs)
        if self._boom:
            raise RuntimeError("db is down")
        return self._rows


@pytest.mark.asyncio
async def test_frequency_accumulates_per_tool():
    store = _Store([
        _outcome(tools=["web_search"]),
        _outcome(tools=["web_search", "web_fetch"]),
    ])
    scores = await score_tools_for_owl(store, "scout", now=_NOW)
    assert scores["web_search"] > scores["web_fetch"]


@pytest.mark.asyncio
async def test_recency_decays_so_a_job_change_is_learned():
    """An old habit must not outrank a current one forever. One dispatch today
    beats one dispatch a half-life ago; that is the demotion mechanism."""
    store = _Store([
        _outcome(tools=["new_tool"], age_days=0.0),
        _outcome(tools=["old_tool"], age_days=DEMOTION_HALF_LIFE_DAYS),
    ])
    scores = await score_tools_for_owl(store, "scout", now=_NOW)
    assert scores["new_tool"] > scores["old_tool"]
    assert scores["old_tool"] == pytest.approx(scores["new_tool"] * 0.5, rel=1e-6)


@pytest.mark.asyncio
async def test_a_failed_turn_is_not_learned():
    """POSITIVE-ONLY LEARNING — the standing operator directive."""
    store = _Store([_outcome(tools=["flaky"], success=False)])
    assert await score_tools_for_owl(store, "scout", now=_NOW) == {}


@pytest.mark.asyncio
async def test_a_turn_with_a_failure_class_is_not_learned():
    store = _Store([_outcome(tools=["flaky"], failure_class="ToolTimeoutError")])
    assert await score_tools_for_owl(store, "scout", now=_NOW) == {}


@pytest.mark.asyncio
async def test_a_user_disliked_approach_is_not_learned():
    """The subtle half of the shared gate, and the reason this reuses
    is_positive_signal instead of checking `success` itself: a hand-rolled
    success check would silently promote an approach the user rejected. The
    tool-outcome miner shipped with exactly that bug once."""
    store = _Store([_outcome(tools=["annoying"], approach_rating="negative")])
    assert await score_tools_for_owl(store, "scout", now=_NOW) == {}


@pytest.mark.asyncio
async def test_an_unscored_turn_still_counts_as_usage():
    """quality_score is None when the critic never got to the turn. That is
    absence of a score, not a low one — requiring a score would narrow the
    signal to whatever the scorer happened to reach."""
    store = _Store([_outcome(tools=["useful"], quality=None)])
    assert (await score_tools_for_owl(store, "scout", now=_NOW))["useful"] > 0


@pytest.mark.asyncio
async def test_a_low_quality_turn_is_excluded():
    store = _Store([_outcome(tools=["sloppy"], quality=0.1)])
    assert await score_tools_for_owl(store, "scout", now=_NOW) == {}


@pytest.mark.asyncio
async def test_no_owl_name_returns_empty_without_touching_the_db():
    store = _Store([_outcome(tools=["x"])])
    assert await score_tools_for_owl(store, "", now=_NOW) == {}
    assert store.calls == []


@pytest.mark.asyncio
async def test_a_db_failure_returns_empty_rather_than_raising():
    """Ordering must never be able to cost a turn its tools. An empty mapping is
    a real fallback, not a degraded one — rank_candidates then orders by name,
    which is deterministic and therefore still stable across turns."""
    store = _Store([], boom=True)
    assert await score_tools_for_owl(store, "scout", now=_NOW) == {}


@pytest.mark.asyncio
async def test_the_lookback_window_is_passed_to_the_store():
    store = _Store([])
    await score_tools_for_owl(store, "scout", now=_NOW, lookback_days=7)
    assert store.calls[0]["owl_name"] == "scout"
    assert store.calls[0]["since_epoch"] == pytest.approx(_NOW - 7 * _DAY)
