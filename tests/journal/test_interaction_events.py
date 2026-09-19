"""``clarify.raised`` is registered correctly (Story 3.3, AD-28), and
``record_clarify_raised`` opens+binds a durable ``question`` item in ONE
transaction (AD-24), no-ops with no ``db_pool``, and never raises -- the same
proof shape ``tests/journal/test_consent_events.py`` uses for
``consent.requested``.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import AttentionClass, Intensity, NeedsYouKind, RecordKind, classify
from stackowl.journal.interaction_events import ClarifyRaisedAttrs, record_clarify_raised
from stackowl.journal.narrator import narrate_full
from stackowl.journal.registry import get_registry

pytestmark = pytest.mark.asyncio


class TestTheTypeIsRegistered:
    def test_clarify_raised_is_interaction_needs_you_question(self) -> None:
        spec = get_registry().get("clarify.raised")
        assert spec.attrs_model is ClarifyRaisedAttrs
        assert spec.record_kind is RecordKind.INTERACTION
        assert spec.emitting_process == "interaction.clarify_gateway"
        assert spec.needs_you_kind is NeedsYouKind.QUESTION
        assert classify("clarify.raised") == (AttentionClass.NEEDS_YOU, Intensity.NORMAL)


class TestNarration:
    def test_names_the_channel(self) -> None:
        spec = get_registry().get("clarify.raised")
        text = narrate_full(
            spec, ClarifyRaisedAttrs(session_key="s1", channel="telegram"), "x",
        )
        assert "telegram" in text


class TestANoneDbPoolIsASilentNoOp:
    async def test_no_db_pool_writes_no_row_and_never_raises(self) -> None:
        item_id = await record_clarify_raised(
            None, clarify_id="clarify-1", session_key="s1", channel="cli",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        assert item_id is None


class TestRecordClarifyRaised:
    """Story 3.3 (AD-28): opens+binds a durable ``question`` item in ONE
    transaction -- the open+bind point ``ClarifyGateway.ask()`` calls for a
    BLOCKING clarify only, the moment its own ``asyncio.Event`` is
    constructed."""

    async def test_opens_and_binds_a_real_question_item(self, tmp_db: DbPool) -> None:
        item_id = await record_clarify_raised(
            tmp_db, clarify_id="clarify-abc", session_key="s1", channel="telegram",
            expires_at="2099-01-01T00:00:00+00:00",
        )

        assert item_id is not None
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == "question"
        assert row["waiter_kind"] == "turn"
        assert row["waiter_id"] == "clarify-abc"
        assert row["expires_at"] == "2099-01-01T00:00:00+00:00"
        assert row["resolved_cursor"] is None
        assert row["dedupe_key"] == "question:owner:clarify-abc"

        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'clarify.raised'",
        )
        assert len(journal_rows) == 1
        assert journal_rows[0]["target_id"] == "clarify-abc"
        opened_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.opened'",
        )
        assert len(opened_rows) == 1

    async def test_two_concurrent_clarifies_each_get_their_own_item(
        self, tmp_db: DbPool,
    ) -> None:
        """The clarify_id itself (already unique per ask() call, minted via
        secrets.token_urlsafe) is the target_id -- two DIFFERENT questions
        for the SAME session must never collide onto one item."""
        item_id_a, item_id_b = await asyncio.gather(
            record_clarify_raised(
                tmp_db, clarify_id="clarify-a", session_key="s1", channel="cli",
                expires_at="2099-01-01T00:00:00+00:00",
            ),
            record_clarify_raised(
                tmp_db, clarify_id="clarify-b", session_key="s1", channel="cli",
                expires_at="2099-01-01T00:00:00+00:00",
            ),
        )

        assert item_id_a is not None
        assert item_id_b is not None
        assert item_id_a != item_id_b
        rows = await tmp_db.fetch_all("SELECT id FROM needs_you WHERE kind = 'question'")
        assert len(rows) == 2

    async def test_a_journal_record_failure_never_raises_and_returns_none(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import interaction_events as interaction_events_module

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(interaction_events_module, "journal_record", _boom)

        item_id = await record_clarify_raised(
            tmp_db, clarify_id="clarify-boom", session_key="s2", channel="cli",
            expires_at="2099-01-01T00:00:00+00:00",
        )

        assert item_id is None
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE kind = 'question'")
        assert rows == []
