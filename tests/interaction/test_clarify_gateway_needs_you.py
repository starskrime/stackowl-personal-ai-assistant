"""Story 3.3 (AD-28) -- ``ClarifyGateway``'s BLOCKING ``ask()``/``wait_for_answer()``
open a durable ``question`` needs_you item, bind the calling coroutine's own
in-memory wait as its ``waiter_kind="turn"`` waiter, and settle it EXCLUSIVELY
through ``needs_you.resolve()``/``needs_you.settle_or_abandon()`` on every
exit -- answered, timed out, cancelled, or abandoned.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.db.pool import DbPool
from stackowl.interaction.clarify_gateway import (
    OUTCOME_ANSWERED,
    OUTCOME_CANCELLED,
    OUTCOME_TIMED_OUT,
    ClarifyGateway,
)
from stackowl.journal import needs_you

pytestmark = pytest.mark.asyncio


async def _open_question_items(tmp_db: DbPool) -> list[dict]:
    return await tmp_db.fetch_all("SELECT * FROM needs_you WHERE kind = 'question'", ())


class TestOpenOnlyForBlocking:
    async def test_blocking_ask_opens_a_question_item(self, tmp_db: DbPool) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

        entry = gw.peek(cid)
        assert entry is not None
        assert entry.needs_you_item_id is not None

        rows = await _open_question_items(tmp_db)
        assert len(rows) == 1
        assert rows[0]["waiter_kind"] == "turn"
        assert rows[0]["waiter_id"] == cid

    async def test_non_blocking_ask_opens_no_item(self, tmp_db: DbPool) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        await gw.ask("s1", "cli", "fav colour?", blocking=False)

        rows = await _open_question_items(tmp_db)
        assert rows == []

    async def test_f71_auto_resolved_clarify_opens_no_item(self, tmp_db: DbPool) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask(
            "s1", "cli", "Proceed?", choices=("Proceed",), blocking=True,
        )

        entry = gw.peek(cid)
        assert entry is not None
        assert entry.needs_you_item_id is None

        rows = await _open_question_items(tmp_db)
        assert rows == []

    async def test_no_db_pool_opens_nothing_and_behaves_as_before(self) -> None:
        gw = ClarifyGateway()
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)
        entry = gw.peek(cid)
        assert entry is not None
        assert entry.needs_you_item_id is None


class TestAnsweredResolvesThroughTheResolver:
    async def test_answer_via_try_resolve_resolves_the_item(self, tmp_db: DbPool) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

        waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
        await asyncio.sleep(0)
        gw.try_resolve("s1", "cli", "blue")
        answer, outcome = await waiter

        assert outcome == OUTCOME_ANSWERED
        assert answer == "blue"
        rows = await _open_question_items(tmp_db)
        assert rows == [] or rows[0]["resolved_cursor"] is not None
        resolved_rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE kind = 'question'",
        )
        assert len(resolved_rows) == 1
        assert resolved_rows[0]["resolved_cursor"] is not None
        assert resolved_rows[0]["answer"] == "blue"
        assert resolved_rows[0]["resolved_by"] == "owner:cli"

    async def test_a_differing_winning_answer_overrides_the_local_one(
        self, tmp_db: DbPool,
    ) -> None:
        """Mirrors the consent-side race test: resolve()'s own returned
        answer -- never the raw local ``entry.answer`` -- is what
        wait_for_answer() returns, even when they differ (a concurrent
        winner answered first)."""
        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)
        entry = gw.peek(cid)
        assert entry is not None and entry.needs_you_item_id is not None

        # A concurrent winner resolves the SAME item directly through
        # needs_you.resolve() first, with a DIFFERENT answer than the one
        # that will land locally via try_resolve.
        async with tmp_db.transaction() as conn:
            winner = await needs_you.resolve(
                conn, item_id=entry.needs_you_item_id, answer="red",
                resolved_by="owner:telegram",
            )
        assert winner.outcome == "resolved"

        waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
        await asyncio.sleep(0)
        gw.try_resolve("s1", "cli", "blue")
        answer, outcome = await waiter

        assert outcome == OUTCOME_ANSWERED
        assert answer == "red", "the WINNING (first) answer must be returned, not the local one"


class TestTimeoutSettlesTheItem:
    async def test_timeout_settles_rather_than_leaving_the_item_open(
        self, tmp_db: DbPool,
    ) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

        answer, outcome = await gw.wait_for_answer(cid, timeout=0.05)

        assert outcome == OUTCOME_TIMED_OUT
        assert answer is None
        rows = await _open_question_items(tmp_db)
        assert len(rows) == 1
        assert rows[0]["resolved_cursor"] is not None, "must settle, never left open"
        assert rows[0]["resolved_by"] == "system:clarify_timeout"


class TestCancelledSettlesBeforeReraising:
    async def test_cancellation_settles_the_item_and_still_reraises(
        self, tmp_db: DbPool,
    ) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

        waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
        await asyncio.sleep(0)
        waiter.cancel()

        with pytest.raises(asyncio.CancelledError):
            await waiter

        rows = await _open_question_items(tmp_db)
        assert len(rows) == 1
        assert rows[0]["resolved_cursor"] is not None
        assert rows[0]["resolved_by"] == "system:clarify_cancelled"


class TestAbandonedWakeSettlesTheItem:
    async def test_cap_one_replace_settles_the_superseded_item(
        self, tmp_db: DbPool,
    ) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        first_cid = await gw.ask("s1", "cli", "first question?", blocking=True)
        waiter = asyncio.ensure_future(gw.wait_for_answer(first_cid, timeout=5.0))
        await asyncio.sleep(0)

        # A second ask() for the SAME session replaces (and abandons) the
        # first pending entry -- its parked waiter wakes TIMED_OUT.
        await gw.ask("s1", "cli", "second question?", blocking=True)
        answer, outcome = await waiter

        assert outcome == OUTCOME_TIMED_OUT
        assert answer is None
        rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE kind = 'question' ORDER BY opened_cursor",
        )
        assert len(rows) == 2
        assert rows[0]["resolved_cursor"] is not None, "the SUPERSEDED item must settle"
        # Story 3.3 patch round 2 -- _abandon_waiter now directly awaits its own
        # settle (no fire-and-forget), so it completes -- and wins -- BEFORE
        # wait_for_answer's own later abandoned-wake settle ever runs (that one
        # is now a harmless no-op against an already-resolved row). The
        # resolved_by value is therefore _abandon_waiter's own reason
        # ("superseded" -- the cap-one replace path), not wait_for_answer's
        # generic "abandoned".
        assert rows[0]["resolved_by"] == "system:clarify_superseded"

    async def test_cancel_pending_settles_the_item_as_cancelled(
        self, tmp_db: DbPool,
    ) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "delete which file?", blocking=True)
        waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
        await asyncio.sleep(0)

        await gw.cancel_pending("s1", "cli")
        answer, outcome = await waiter

        assert outcome == OUTCOME_CANCELLED
        assert answer is None
        rows = await _open_question_items(tmp_db)
        assert len(rows) == 1
        assert rows[0]["resolved_cursor"] is not None
        # Story 3.3 patch round 2 -- see the identical note in
        # test_cap_one_replace_settles_the_superseded_item above: _abandon_waiter's
        # own directly-awaited settle now wins deterministically, using ITS own
        # reason ("cancel_pending"), not wait_for_answer's generic "cancelled".
        assert rows[0]["resolved_by"] == "system:clarify_cancel_pending"


class TestAbandonBeforeParkSettlesDirectly:
    """Regression: an entry can be abandoned via a SYNC-surface caller
    (``clear_session``/``cancel_pending``/``clear_all``/``sweep_expired``,
    all routed through ``_abandon_waiter``) BEFORE ``wait_for_answer`` ever
    parks on it -- a real scheduler window, since ``ask()`` itself awaits
    ``record_clarify_raised()``/``adapter.send_clarify()`` before returning.

    An earlier patch round settled this via a fire-and-forget
    ``asyncio.ensure_future(needs_you.settle_or_abandon(...))`` task from
    ``_abandon_waiter``, which does NOT reliably settle before the caller
    moves on -- the untracked task could still be pending when, e.g., a
    test's DB connection closed during teardown, producing an unhandled
    ``sqlite3.ProgrammingError``. ``_abandon_waiter`` and its callers are now
    ``async`` and directly ``await`` the settle, so it is guaranteed
    complete the moment the call returns -- no ``asyncio.sleep(0)`` polling
    required to observe it.
    """

    async def test_clear_session_before_park_settles_immediately(
        self, tmp_db: DbPool,
    ) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

        entry = gw.peek(cid)
        assert entry is not None and entry.needs_you_item_id is not None

        # wait_for_answer() is NEVER called -- the waiter is abandoned before
        # ever parking (the exact real-world race this test proves against).
        dropped = await gw.clear_session("s1")

        assert dropped == [cid]
        rows = await _open_question_items(tmp_db)
        assert len(rows) == 1
        assert rows[0]["resolved_cursor"] is not None, (
            "the item must be settled the instant clear_session() returns, "
            "with no scheduler yield needed"
        )
        assert rows[0]["resolved_by"] == "system:clarify_clear_session"

    async def test_cancel_pending_before_park_settles_immediately(
        self, tmp_db: DbPool,
    ) -> None:
        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "delete which file?", blocking=True)

        entry = gw.peek(cid)
        assert entry is not None and entry.needs_you_item_id is not None

        # wait_for_answer() is NEVER called here either.
        cancelled_id = await gw.cancel_pending("s1", "cli")

        assert cancelled_id == cid
        rows = await _open_question_items(tmp_db)
        assert len(rows) == 1
        assert rows[0]["resolved_cursor"] is not None, (
            "the item must be settled the instant cancel_pending() returns, "
            "with no scheduler yield needed"
        )
        assert rows[0]["resolved_by"] == "system:clarify_cancel_pending"


class TestJournalFailureSwallowPaths:
    """Verification-gap: a journal failure at either the open+bind point or
    the resolve/settle point must never crash the clarify flow."""

    async def test_open_bind_failure_still_delivers_the_question(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import interaction_events as interaction_events_module

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(interaction_events_module, "journal_record", _boom)

        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

        entry = gw.peek(cid)
        assert entry is not None
        assert entry.needs_you_item_id is None, "a failed open+bind must leave the entry unbound"

        waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
        await asyncio.sleep(0)
        gw.try_resolve("s1", "cli", "blue")
        answer, outcome = await waiter

        assert outcome == OUTCOME_ANSWERED
        assert answer == "blue", "unbound entries fall back to the raw local answer"

    async def test_resolve_failure_still_returns_the_local_answer(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A ``needs_you.resolve()`` failure on the ANSWERED path must never
        crash the parked tool call -- it falls back to the raw local answer,
        the same B5-style guarantee every other journal write in this
        codebase gives its caller."""
        import stackowl.journal.needs_you as needs_you_module

        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated resolve failure")

        monkeypatch.setattr(needs_you_module, "resolve", _boom)

        waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
        await asyncio.sleep(0)
        gw.try_resolve("s1", "cli", "blue")
        answer, outcome = await waiter

        assert outcome == OUTCOME_ANSWERED
        assert answer == "blue"

    async def test_a_settle_path_journal_failure_still_times_out_cleanly(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The SETTLE path's own underlying journal write failing (not
        ``settle_or_abandon`` itself raising -- its own contract forbids
        that, proven in test_needs_you.py -- but the DB op it wraps) must
        never crash the timeout branch."""
        import stackowl.journal.needs_you as needs_you_module

        async def _flaky_expire_item(conn, **kwargs):  # noqa: ANN001, ANN003, ARG001
            raise RuntimeError("simulated settle-path journal failure")

        monkeypatch.setattr(needs_you_module, "_expire_item", _flaky_expire_item)

        gw = ClarifyGateway(db_pool=tmp_db)
        cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

        answer, outcome = await gw.wait_for_answer(cid, timeout=0.05)

        assert outcome == OUTCOME_TIMED_OUT
        assert answer is None
