"""``channel.message_received`` is registered correctly (Story 2.9), and
``record_channel_message_received`` is atomic with its own transaction
(AD-24), no-ops with no ``db_pool``, and redacts secrets through a REAL
emitter (NFR33) -- the same proof shape ``tests/journal/test_delivery_events.py``
uses for this story's other two types.
"""

from __future__ import annotations

import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import AttentionClass, RecordKind, classify
from stackowl.journal.channel_events import (
    ChannelMessageReceivedAttrs,
    record_channel_message_received,
)
from stackowl.journal.narrator import narrate_full
from stackowl.journal.registry import get_registry

pytestmark = pytest.mark.asyncio

# Synthetic canary only -- never a real credential.
BEARER_CANARY = "Bearer sk-canary1234567890abcdefghijklmno"


class TestTheTypeIsRegistered:
    def test_channel_message_received_is_channel_ambient(self) -> None:
        spec = get_registry().get("channel.message_received")
        assert spec.attrs_model is ChannelMessageReceivedAttrs
        assert spec.record_kind is RecordKind.CHANNEL
        assert spec.emitting_process == "startup.orchestrator"
        assert classify("channel.message_received") == (AttentionClass.AMBIENT, None)


class TestNarration:
    def test_names_the_channel(self) -> None:
        spec = get_registry().get("channel.message_received")
        text = narrate_full(
            spec,
            ChannelMessageReceivedAttrs(channel="telegram", session_key="s1"),
            "x",
        )
        assert "telegram" in text


class TestAttrsNeverCarryFreeText:
    def test_an_undeclared_field_is_refused(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ChannelMessageReceivedAttrs(
                channel="telegram", session_key="s1",
                message_text="free text must never fit here",  # type: ignore[call-arg]
            )


class TestANoneDbPoolIsASilentNoOp:
    async def test_no_db_pool_writes_no_row_and_never_raises(self) -> None:
        await record_channel_message_received(
            None, channel="cli", session_key="s1", trace_id="t1",
        )


class TestTheHelperCommitsExactlyOneRowInEachTable:
    async def test_received(self, tmp_db: DbPool) -> None:
        await record_channel_message_received(
            tmp_db, channel="telegram", session_key="s1", trace_id="trace-1",
        )
        ingress_rows = await tmp_db.fetch_all(
            "SELECT * FROM channel_ingress_records WHERE session_key = ?", ("s1",)
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'channel.message_received' "
            "AND target_id = ?",
            ("telegram",),
        )
        assert len(ingress_rows) == 1
        assert len(journal_rows) == 1
        row = journal_rows[0]
        assert row["outcome"] == "ok"
        assert row["attention"] == "ambient"
        assert row["trace_id"] == "trace-1"
        ref = json.loads(row["record_ref"])
        assert ref["kind"] == "sqlite"
        assert ref["locator"]["table"] == "channel_ingress_records"
        assert ref["locator"]["id"] == ingress_rows[0]["id"]
        attrs = json.loads(row["attrs"])
        assert attrs["channel"] == "telegram"
        assert attrs["session_key"] == "s1"


class TestTheHelperIsAtomicWithItsOwnTransaction:
    async def test_a_journal_record_failure_leaves_neither_row(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import channel_events as channel_events_module

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(channel_events_module, "journal_record", _boom)

        # Never raises out (B5).
        await record_channel_message_received(
            tmp_db, channel="cli", session_key="rollback-key", trace_id="t2",
        )

        ingress_rows = await tmp_db.fetch_all(
            "SELECT * FROM channel_ingress_records WHERE session_key = ?",
            ("rollback-key",),
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'channel.message_received'",
        )
        assert ingress_rows == []
        assert journal_rows == []

    async def test_a_raw_insert_failure_degrades_health_directly(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import stackowl.journal.channel_events as channel_events_module
        from stackowl.journal import health as journal_health

        monkeypatch.setattr(
            channel_events_module, "_INSERT_CHANNEL_INGRESS_RECORD_SQL",
            "INSERT INTO no_such_table (id) VALUES (?)",
        )
        await record_channel_message_received(
            tmp_db, channel="cli", session_key="broken-insert-key", trace_id="t3",
        )
        snap = journal_health.JournalHealthContributor()
        status = await snap.health_check()
        assert status.status == "degraded"


class TestCanarySecretsAreRedactedThroughARealEmitter:
    """NFR33: driven through the REAL recording helper -- no canary ever
    appears unredacted in ``channel_ingress_records``/``journal_events.attrs``."""

    async def test_session_key(self, tmp_db: DbPool) -> None:
        await record_channel_message_received(
            tmp_db, channel="cli", session_key=BEARER_CANARY, trace_id="t4",
        )
        rows = await tmp_db.fetch_all(
            "SELECT session_key FROM channel_ingress_records",
        )
        assert len(rows) == 1
        assert BEARER_CANARY not in rows[0]["session_key"]

        journal_rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE type = 'channel.message_received'",
        )
        stored = journal_rows[0]["attrs"]
        assert BEARER_CANARY not in stored
        assert json.loads(stored)["_redacted"] is True
