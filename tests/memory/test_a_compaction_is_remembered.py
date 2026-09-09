"""The reuse path was reachable code with no producer, for six days.

Bakir, 2026-09-09: *"First of all platfomr does not remember when last time did
compaction and now doing for my each request."*

MEASURED across every retained log: **34 compression events over six days, and
`had_prior_summary=false` on EVERY ONE.** Never once true. Outside
`conversation_compressor.py` the string `prior_summary` appeared in exactly ONE
place in `src/` — the log line that reports whether there was one. `select()` reads
it, `apply()` branches on it, and nothing anywhere wrote one. Defect shape 1 in the
mirror: the catalogue names "a write with no reader", and this was a **read with no
writer**.

WHY IT COULD NOT BE WIRED, which is the part worth keeping. `select()` finds a prior
summary only if it arrives INSIDE the history, as a message carrying
`SUMMARY_MARKER`. History comes from `recent_conversation_turns`, whose unit is a
user/assistant TURN PAIR — and a summary is neither half of a turn. There was
nowhere in the store's shape to put one, so the feature could be designed and could
not be wired. `conversation_summaries` (migration 0141) is that place, keyed by the
same scope key the turns use.

AND IT CARRIES A CORRECTION TO MY OWN PREVIOUS CHANGE. DEBT-248 made the history
budget window-relative, which silently made the SUMMARY ceiling window-relative too:
`SUMMARY_BUDGET_SHARE` was calibrated against a fixed 12,000 (a 3,000-token ceiling)
and 0.25 of 196,608 is 49,152 — a 16x widening I shipped without measuring. Nothing
broke, because the clip has never fired in the entire retained window, which is
exactly why it had to be measured rather than noticed.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory import conversation_compressor as cc
from stackowl.memory.bridge import NullMemoryBridge
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.providers.base import Message


class TestTheCeilingDEBT248Widened:
    """0.25 of a 196,608 budget is 49,152. That is not a summary."""

    def test_the_ceiling_is_capped_however_large_the_window(self) -> None:
        big = cc.history_budget(262_144)
        assert big == 196_608
        assert int(big * cc.SUMMARY_BUDGET_SHARE) == 49_152, (
            "the share no longer produces the value this cap exists to stop"
        )
        text = "line\n" * 40_000
        clipped = cc._bounded(text, big)  # noqa: SLF001
        from stackowl.parliament.token_estimate import estimate_tokens

        assert estimate_tokens(clipped) <= cc.SUMMARY_MAX_TOKENS + 50

    def test_a_lean_model_still_gets_the_SMALLER_of_the_two(self) -> None:
        """The cap must not become a floor: a small window still shrinks the ceiling."""
        small = cc.history_budget(8_192)
        assert int(small * cc.SUMMARY_BUDGET_SHARE) < cc.SUMMARY_MAX_TOKENS
        text = "line\n" * 40_000
        from stackowl.parliament.token_estimate import estimate_tokens

        assert estimate_tokens(cc._bounded(text, small)) < cc.SUMMARY_MAX_TOKENS  # noqa: SLF001


@pytest.mark.asyncio
class TestTheSummarySurvivesTheTurn:
    async def test_a_stored_summary_comes_back(self, tmp_db: DbPool) -> None:
        """THE WRITER THAT DID NOT EXIST."""
        bridge = SqliteMemoryBridge(db=tmp_db)
        assert await bridge.get_conversation_summary("lane-1") is None

        await bridge.set_conversation_summary("lane-1", "RESOLVED: the thing")
        assert await bridge.get_conversation_summary("lane-1") == "RESOLVED: the thing"

    async def test_a_later_compaction_replaces_the_earlier_one(
        self, tmp_db: DbPool
    ) -> None:
        """One row per conversation. Appending would stack a copy per turn, which is
        the "no decay" shape — anything that only appends poisons its reader."""
        bridge = SqliteMemoryBridge(db=tmp_db)
        await bridge.set_conversation_summary("lane-1", "first")
        await bridge.set_conversation_summary("lane-1", "second")

        assert await bridge.get_conversation_summary("lane-1") == "second"
        rows = await tmp_db.fetch_all("SELECT COUNT(*) AS n FROM conversation_summaries")
        assert rows[0]["n"] == 1

    async def test_conversations_do_not_read_each_others_summaries(
        self, tmp_db: DbPool
    ) -> None:
        """Keyed by the same scope key the TURNS use, so a summary lives in exactly
        the bucket its conversation does."""
        bridge = SqliteMemoryBridge(db=tmp_db)
        await bridge.set_conversation_summary("lane-1", "mine")

        assert await bridge.get_conversation_summary("lane-2") is None

    async def test_a_bridge_that_cannot_store_one_says_so_rather_than_lying(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A silent empty body makes "stored" and "cannot store" look identical to the
        next reader — the ambiguity that let this sit unwired for six days."""
        import logging

        bridge = NullMemoryBridge()
        with caplog.at_level(logging.DEBUG):
            await bridge.set_conversation_summary("lane-1", "text")

        assert await bridge.get_conversation_summary("lane-1") is None
        assert any("noop" in r.getMessage() for r in caplog.records)


class TestTheEngineFindsWhatWasPutBack:
    def test_a_restored_summary_is_seen_as_a_PRIOR_summary(self) -> None:
        """END TO END ON THE EXISTING CONTRACT. `as_message` is how the compressor
        marks a summary and `select` is what looks for the marker; putting the stored
        text back through `as_message` is what makes the two meet."""
        stored = "RESOLVED: earlier context"
        history = [cc.as_message(stored)] + [
            Message(role="user", content="x " * 4_000) for _ in range(30)
        ]
        selection = cc.select(history, budget_tokens=12_000)

        assert selection.prior_summary is not None
        assert stored in selection.prior_summary
        assert all(
            not (m.content or "").startswith(cc.SUMMARY_MARKER)
            for m in (*selection.head, *selection.middle, *selection.tail)
        ), "the marker message was left in the turns as well as extracted"


@pytest.mark.tripwire
def test_classify_actually_reads_and_writes_the_summary() -> None:
    """WIRED, NOT DECORATION.

    Both halves are asserted because either alone is useless: a reader with no writer
    is the defect being fixed, and a writer with no reader stores rows nobody uses.
    """
    from stackowl.pipeline.steps import classify

    src = inspect.getsource(classify._compress_history)  # noqa: SLF001
    assert "get_conversation_summary" in src, "the stored summary is never read back"
    assert "set_conversation_summary" in src, "the summary is still never written"
    assert "cc.as_message(prior)" in src, (
        "the summary is read but not put back where select() looks for it"
    )


class TestTheMarkerDoesNotStack:
    """UNREACHABLE UNTIL THE WRITER EXISTED, which is why it survived.

    `select()` extracted `m.content` INCLUDING `SUMMARY_MARKER` into `prior_summary`,
    and `apply()` passed that back through `as_message()`, which prepends the marker
    again. `prior_summary` was None on all 34 recorded compactions, so the round trip
    never happened and the doubling never showed. It COMPOUNDS — one marker per reuse,
    forever.

    The existing comment in `select` anticipated stacking and guarded the wrong thing:
    "never kept alongside it, or every compaction would stack another copy" is about
    stacking MESSAGES. The markers stacked instead.
    """

    def test_one_round_trip_leaves_exactly_one_marker(self) -> None:
        history = [cc.as_message("OLD"), Message(role="user", content="t")]
        out = cc.apply(cc.select(history, budget_tokens=12_000), None, budget_tokens=12_000)

        assert out[0].content.count(cc.SUMMARY_MARKER) == 1, out[0].content

    def test_it_does_not_compound_over_repeated_reuse(self) -> None:
        """The property that matters: ten compactions, still one marker."""
        history = [cc.as_message("OLD"), Message(role="user", content="t")]
        for _ in range(10):
            out = cc.apply(
                cc.select(history, budget_tokens=12_000), None, budget_tokens=12_000
            )
            history = [*out, Message(role="user", content="t")]

        markers = sum((m.content or "").count(cc.SUMMARY_MARKER) for m in history)
        assert markers == 1, f"{markers} markers after ten reuses — it is stacking again"

    def test_the_prior_summary_is_the_TEXT_not_the_marked_message(self) -> None:
        """What `build_prompt` folds into the next summarization must be the summary,
        not a marker line the model then has to interpret."""
        selection = cc.select(
            [cc.as_message("RESOLVED: a thing"), Message(role="user", content="t")],
            budget_tokens=12_000,
        )
        assert selection.prior_summary == "RESOLVED: a thing"
