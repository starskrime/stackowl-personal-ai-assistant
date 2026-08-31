"""The prefetch decision cannot be made, because the question is not recorded.

Phase 5's first-ranked mechanism is a speculative memory prefetch, and the research
note that proposed it is explicit that its value is unproven: "The prefetch saving
is an argument, not yet a measurement — the 414 synchronous searches are measured,
the saving is not."

MEASURING IT TODAY FOUND TWO THINGS.

ONE — THE PREMISE MOVED. Of 171 ``memory`` searches across the retained logs,
**165 (96.5%) run on MACHINE lanes**::

    118  (69.0%)  incident RCA
     37  (21.6%)  owl:*:recovery:*
      6  ( 3.5%)  goal
      4  ( 2.3%)  owl:*:objective:*
      6  ( 3.5%)  a human chat lane

The recommendation was framed as a user-facing win — "each costs the whole re-sent
prefix" — but nobody is waiting on 96.5% of these, and a prefetch keyed on TURN
START would not fire usefully on an incident lane whose input is an RCA prompt. Six
searches in four days is the user-facing population. That is ESC-94.

TWO — AND THE DECIDING QUESTION IS UNANSWERABLE. Whether a prefetch would have
SATISFIED a search depends on how close the model's query is to the turn's input
text, and the log records only ``{'action': 'search'}``. The query is not there. So
the one measurement that could settle the prefetch's value cannot be taken from
four days of production traffic.

This programme has already paid for that exact shape: "If a log line is the
evidence for a claim, it must be INFO — and run the closing query BEFORE you need
it." This is that line, added before it is needed rather than after.

BOUNDED, NOT VERBATIM. The query is model-authored text derived from a user turn,
so it is truncated the same way ``describe_parse_failure``'s ``raw_preview`` and
the reflection failure record are — enough to compare against the turn's input,
never a second full copy of the conversation in the log.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.tools.knowledge.memory import _QUERY_PREVIEW_CHARS, MemoryTool

pytestmark = pytest.mark.asyncio


class _Bridge:
    async def recall(self, query: str, limit: int = 10) -> list:
        return []


def _tool() -> MemoryTool:
    return MemoryTool()


async def test_a_search_records_the_query_it_ran(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The missing evidence. Without it the prefetch's value is unmeasurable."""
    tool = _tool()
    monkeypatch.setattr(tool, "_bridge", lambda: _Bridge(), raising=False)

    with caplog.at_level(logging.INFO):
        await tool.execute(action="search", query="what is my dentist called")

    entries = [r for r in caplog.records if "memory.execute: entry" in r.getMessage()]
    assert entries, "the entry line vanished"
    fields = getattr(entries[-1], "_fields", {})
    assert fields.get("action") == "search"
    assert "dentist" in str(fields.get("query") or ""), (
        "the query is still not recorded — the one measurement the prefetch "
        "decision needs cannot be taken"
    )


async def test_a_LONG_query_is_bounded(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A log line must not become a second full copy of the conversation."""
    tool = _tool()
    monkeypatch.setattr(tool, "_bridge", lambda: _Bridge(), raising=False)
    long_query = "remember " * 500

    with caplog.at_level(logging.INFO):
        await tool.execute(action="search", query=long_query)

    entries = [r for r in caplog.records if "memory.execute: entry" in r.getMessage()]
    recorded = str(getattr(entries[-1], "_fields", {}).get("query") or "")
    assert len(recorded) <= _QUERY_PREVIEW_CHARS


async def test_a_NON_search_action_records_no_query(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`add` and `replace` carry the user's own words as CONTENT, not a query.
    Logging those here would put durable memory text into the log on every write,
    which is a different decision and not this one."""
    with caplog.at_level(logging.INFO):
        await _tool().execute(action="add", content="my dentist is Dr Antoon")

    entries = [r for r in caplog.records if "memory.execute: entry" in r.getMessage()]
    fields = getattr(entries[-1], "_fields", {})
    assert fields.get("action") == "add"
    assert "query" not in fields, "a write's content is not a search query"


async def test_an_EMPTY_search_still_logs_its_action(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The refusal path must stay diagnosable — it is how a malformed call is seen."""
    with caplog.at_level(logging.INFO):
        result = await _tool().execute(action="search", query="  ")

    # The field is `success`; without a bridge it refuses earlier than the blank
    # query does, which is the same refusal path and the same diagnosability point.
    assert result.success is False
    entries = [r for r in caplog.records if "memory.execute: entry" in r.getMessage()]
    assert getattr(entries[-1], "_fields", {}).get("action") == "search"


def test_the_preview_bound_is_stated_once() -> None:
    """One constant, so the bound cannot drift between the log line and this test."""
    assert 0 < _QUERY_PREVIEW_CHARS <= 200
