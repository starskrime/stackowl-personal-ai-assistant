"""``consent.decided`` is registered correctly (Story 2.8), and
``record_consent_decision`` is atomic with its own transaction (AD-24),
no-ops with no ``db_pool``, and redacts secrets through a REAL emitter
(NFR33) -- the same proof shape ``tests/journal/test_turn_events.py`` uses
for Story 2.7's types.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.journal import AttentionClass, Intensity, NeedsYouKind, RecordKind, classify
from stackowl.journal.consent_events import (
    ConsentChannelUnreachableAttrs,
    ConsentDecisionAttrs,
    ConsentRequestedAttrs,
    record_channel_unreachable,
    record_consent_decision,
    record_consent_requested,
)
from stackowl.journal.narrator import narrate_full
from stackowl.journal.registry import get_registry

pytestmark = pytest.mark.asyncio

# Synthetic canary only -- never a real credential.
BEARER_CANARY = "Bearer sk-canary1234567890abcdefghijklmno"


class TestTheTypeIsRegistered:
    def test_consent_decided_is_consent_ambient(self) -> None:
        spec = get_registry().get("consent.decided")
        assert spec.attrs_model is ConsentDecisionAttrs
        assert spec.record_kind is RecordKind.CONSENT
        assert spec.emitting_process == "tools.consent"
        assert classify("consent.decided") == (AttentionClass.AMBIENT, None)

    def test_consent_requested_is_consent_needs_you_approval(self) -> None:
        """Story 3.3 (AD-28)."""
        spec = get_registry().get("consent.requested")
        assert spec.attrs_model is ConsentRequestedAttrs
        assert spec.record_kind is RecordKind.CONSENT
        assert spec.emitting_process == "tools.consent"
        assert spec.needs_you_kind is NeedsYouKind.APPROVAL
        assert classify("consent.requested") == (AttentionClass.NEEDS_YOU, Intensity.NORMAL)

    def test_channel_unreachable_is_consent_needs_you_incident(self) -> None:
        """Story 3.4."""
        spec = get_registry().get("consent.channel_unreachable")
        assert spec.attrs_model is ConsentChannelUnreachableAttrs
        assert spec.record_kind is RecordKind.CONSENT
        assert spec.emitting_process == "tools.consent"
        assert spec.needs_you_kind is NeedsYouKind.INCIDENT
        assert classify("consent.channel_unreachable") == (AttentionClass.NEEDS_YOU, Intensity.HIGH)


class TestNarration:
    def test_names_the_tool_and_reason(self) -> None:
        spec = get_registry().get("consent.decided")
        text = narrate_full(
            spec,
            ConsentDecisionAttrs(
                tool_name="shell", channel="telegram", reason="user_once",
                scope="once", category=None, decision="allow",
            ),
            "x",
        )
        assert "shell" in text
        assert "user_once" in text


class TestConsentDecisionAttrsNeverCarriesFreeText:
    def test_an_undeclared_field_is_refused(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ConsentDecisionAttrs(
                tool_name="shell", channel="telegram", reason="user_once",
                scope=None, category=None, decision="allow",
                summary="free text must never fit here",  # type: ignore[call-arg]
            )

    def test_decision_only_accepts_the_closed_vocabulary(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ConsentDecisionAttrs(
                tool_name="shell", channel="telegram", reason="user_once",
                scope=None, category=None,
                decision="maybe",  # type: ignore[arg-type]
            )


class TestANoneDbPoolIsASilentNoOp:
    async def test_no_db_pool_writes_no_row_and_never_raises(self) -> None:
        await record_consent_decision(
            None, tool_name="shell", channel="cli", session_key="s1",
            category=None, reason="tier_auto", scope=None, allowed=True,
        )


class TestTheHelperCommitsExactlyOneRowInEachTable:
    async def test_allowed_decision(self, tmp_db: DbPool) -> None:
        await record_consent_decision(
            tmp_db, tool_name="shell", channel="telegram", session_key="s1",
            category=None, reason="user_once", scope="once", allowed=True,
        )

        consent_rows = await tmp_db.fetch_all(
            "SELECT * FROM consent_decision_records WHERE tool_name = ?", ("shell",)
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'consent.decided' AND target_id = ?",
            ("shell",),
        )
        assert len(consent_rows) == 1
        assert len(journal_rows) == 1
        row = journal_rows[0]
        assert row["outcome"] == "ok"
        assert row["attention"] == "ambient"
        ref = json.loads(row["record_ref"])
        assert ref["kind"] == "sqlite"
        assert ref["locator"]["table"] == "consent_decision_records"
        assert ref["locator"]["id"] == consent_rows[0]["id"]
        assert consent_rows[0]["decision"] == "allow"
        assert consent_rows[0]["channel"] == "telegram"
        attrs = json.loads(row["attrs"])
        assert attrs["decision"] == "allow"
        assert attrs["tool_name"] == "shell"

    async def test_denied_decision(self, tmp_db: DbPool) -> None:
        await record_consent_decision(
            tmp_db, tool_name="ha_call_service", channel="cli", session_key="s2",
            category="lock", reason="not_approved", scope=None, allowed=False,
        )

        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'consent.decided' AND target_id = ?",
            ("ha_call_service",),
        )
        assert len(journal_rows) == 1
        assert journal_rows[0]["outcome"] == "failed"
        attrs = json.loads(journal_rows[0]["attrs"])
        assert attrs["decision"] == "deny"
        assert attrs["category"] == "lock"

        consent_rows = await tmp_db.fetch_all(
            "SELECT decision FROM consent_decision_records WHERE tool_name = ?",
            ("ha_call_service",),
        )
        assert consent_rows[0]["decision"] == "deny"

    async def test_no_trace_id_still_records(self, tmp_db: DbPool) -> None:
        """Unlike Story 2.7's turn-scoped types, a consent decision is
        recorded REGARDLESS of trace presence -- owl_build.py's direct
        `gate.policy.request()` call site may have none."""
        await record_consent_decision(
            tmp_db, tool_name="shell", channel="cli", session_key="s3",
            category=None, reason="tier_auto", scope=None, allowed=True,
        )
        rows = await tmp_db.fetch_all(
            "SELECT trace_id FROM journal_events WHERE type = 'consent.decided'",
        )
        assert len(rows) == 1
        assert rows[0]["trace_id"] is None

    async def test_trace_id_rides_the_ambient_context_when_present(
        self, tmp_db: DbPool,
    ) -> None:
        token = TraceContext.start(trace_id="trace-consent-1")
        try:
            await record_consent_decision(
                tmp_db, tool_name="shell", channel="cli", session_key="s4",
                category=None, reason="tier_auto", scope=None, allowed=True,
            )
        finally:
            TraceContext.reset(token)

        rows = await tmp_db.fetch_all(
            "SELECT trace_id FROM journal_events WHERE type = 'consent.decided'",
        )
        assert rows[0]["trace_id"] == "trace-consent-1"


class TestTheHelperIsAtomicWithItsOwnTransaction:
    async def test_a_journal_record_failure_leaves_neither_row(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import consent_events as consent_events_module

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(consent_events_module, "journal_record", _boom)

        # Never raises out (B5) -- `ConsentPolicy._finalize` must return its
        # `allowed` bool regardless.
        await record_consent_decision(
            tmp_db, tool_name="rollback-tool", channel="cli", session_key="s5",
            category=None, reason="tier_auto", scope=None, allowed=True,
        )

        consent_rows = await tmp_db.fetch_all(
            "SELECT * FROM consent_decision_records WHERE tool_name = ?", ("rollback-tool",)
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'consent.decided'",
        )
        assert consent_rows == []
        assert journal_rows == []

    async def test_a_raw_insert_failure_degrades_health_directly(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The raw `consent_decision_records` insert failing BEFORE
        journal.record() ever runs means recorder.py's own failure path
        never fires -- `record_consent_decision` must degrade health itself
        for exactly that case (mirrors turn_events.py's own precedent)."""
        from stackowl.journal import health as journal_health

        await record_consent_decision(
            tmp_db, tool_name="dup-tool", channel="cli", session_key="s6",
            category=None, reason="tier_auto", scope=None, allowed=True,
        )
        # A second insert with a colliding primary key id would collide, but
        # ids are UUIDs -- instead force the raw insert to fail directly via
        # a broken table name through monkeypatching the SQL constant.
        import stackowl.journal.consent_events as consent_events_module

        monkeypatch.setattr(
            consent_events_module, "_INSERT_CONSENT_DECISION_SQL",
            "INSERT INTO no_such_table (id) VALUES (?)",
        )
        await record_consent_decision(
            tmp_db, tool_name="broken-insert-tool", channel="cli", session_key="s7",
            category=None, reason="tier_auto", scope=None, allowed=True,
        )
        snap = journal_health.JournalHealthContributor()
        status = await snap.health_check()
        assert status.status == "degraded"


class TestCanarySecretsAreRedactedThroughARealEmitter:
    """NFR33: driven through the REAL recording helper -- no canary ever
    appears unredacted in ``journal_events.attrs``."""

    async def test_consent_decided(self, tmp_db: DbPool) -> None:
        await record_consent_decision(
            tmp_db, tool_name=BEARER_CANARY, channel="cli", session_key="s8",
            category=None, reason="tier_auto", scope=None, allowed=True,
        )

        rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE type = 'consent.decided'",
        )
        assert len(rows) == 1
        stored = rows[0]["attrs"]
        assert BEARER_CANARY not in stored
        assert json.loads(stored)["_redacted"] is True


class TestRecordConsentRequested:
    """Story 3.3 (AD-28): ``record_consent_requested`` opens+binds a durable
    ``approval`` item in ONE transaction -- the open+bind point
    ``ConsentPolicy.request()`` calls the moment it starts waiting."""

    async def test_no_db_pool_is_a_silent_no_op(self) -> None:
        item_id = await record_consent_requested(
            None, tool_name="shell", channel="cli", session_key="s1",
            category=None, expires_at="2099-01-01T00:00:00+00:00",
        )
        assert item_id is None

    async def test_opens_and_binds_a_real_approval_item(self, tmp_db: DbPool) -> None:
        item_id = await record_consent_requested(
            tmp_db, tool_name="shell", channel="telegram", session_key="s1",
            category=None, expires_at="2099-01-01T00:00:00+00:00",
        )

        assert item_id is not None
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == "approval"
        assert row["waiter_kind"] == "turn"
        assert row["waiter_id"] is not None
        assert row["expires_at"] == "2099-01-01T00:00:00+00:00"
        assert row["resolved_cursor"] is None

        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'consent.requested'",
        )
        assert len(journal_rows) == 1
        opened_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.opened'",
        )
        assert len(opened_rows) == 1

    async def test_two_concurrent_requests_for_the_same_session_each_get_their_own_item(
        self, tmp_db: DbPool,
    ) -> None:
        """Review pass 1 Group C -- the regression test for the cross-request
        -contamination bug this pass found: TWO distinct, concurrent
        `ConsentPolicy.request()` calls for the SAME (or both empty)
        `session_key`, for DIFFERENT tools, must each get their OWN item, not
        collide onto one via a `target_id` scoped to the bare session_key."""
        item_id_a, item_id_b = await asyncio.gather(
            record_consent_requested(
                tmp_db, tool_name="shell", channel="cli", session_key="",
                category=None, expires_at="2099-01-01T00:00:00+00:00",
            ),
            record_consent_requested(
                tmp_db, tool_name="execute_code", channel="cli", session_key="",
                category=None, expires_at="2099-01-01T00:00:00+00:00",
            ),
        )

        assert item_id_a is not None
        assert item_id_b is not None
        assert item_id_a != item_id_b, (
            "two distinct concurrent decisions must never collapse onto the "
            "same needs_you item"
        )
        rows = await tmp_db.fetch_all("SELECT id FROM needs_you WHERE kind = 'approval'")
        assert len(rows) == 2

    async def test_a_journal_record_failure_never_raises_and_returns_none(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import consent_events as consent_events_module

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(consent_events_module, "journal_record", _boom)

        item_id = await record_consent_requested(
            tmp_db, tool_name="rollback-tool", channel="cli", session_key="s9",
            category=None, expires_at="2099-01-01T00:00:00+00:00",
        )

        assert item_id is None
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE kind = 'approval'")
        assert rows == []


class TestRecordChannelUnreachable:
    """Story 3.4 — ``record_channel_unreachable`` opens a durable ``incident``
    item, deduplicated per channel, whenever ``RoutingPrompter`` finds no
    prompter (and no default) for a channel."""

    async def test_no_db_pool_is_a_silent_no_op(self) -> None:
        await record_channel_unreachable(None, channel="discord", tool_name="shell")

    async def test_opens_one_incident_item(self, tmp_db: DbPool) -> None:
        await record_channel_unreachable(tmp_db, channel="discord", tool_name="shell")

        rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE kind = 'incident' AND resolved_cursor IS NULL"
        )
        assert len(rows) == 1

        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'consent.channel_unreachable'",
        )
        assert len(journal_rows) == 1
        assert journal_rows[0]["outcome"] == "failed"
        assert journal_rows[0]["attention"] == "needs_you"
        assert journal_rows[0]["intensity"] == "high"
        attrs = json.loads(journal_rows[0]["attrs"])
        assert attrs["channel"] == "discord"
        assert attrs["tool_name"] == "shell"

    async def test_two_calls_for_the_same_channel_dedupe_to_ONE_open_item(
        self, tmp_db: DbPool,
    ) -> None:
        """Mirrors Story 3.1's ``ON CONFLICT ... DO NOTHING`` dedupe — the
        SAME channel must never open a second incident while one is still
        open (spec Code Map: 'per-channel deduped via target_id=channel')."""
        await record_channel_unreachable(tmp_db, channel="whatsapp", tool_name="shell")
        await record_channel_unreachable(tmp_db, channel="whatsapp", tool_name="send_file")

        rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE kind = 'incident' AND resolved_cursor IS NULL"
        )
        assert len(rows) == 1

    async def test_different_channels_each_get_their_own_item(self, tmp_db: DbPool) -> None:
        await record_channel_unreachable(tmp_db, channel="discord", tool_name="shell")
        await record_channel_unreachable(tmp_db, channel="whatsapp", tool_name="shell")

        rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE kind = 'incident' AND resolved_cursor IS NULL"
        )
        assert len(rows) == 2

    async def test_a_journal_record_failure_never_raises(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import consent_events as consent_events_module

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(consent_events_module, "journal_record", _boom)

        # Never raises (B5) — a journaling failure must never turn a
        # fail-closed deny into something louder.
        await record_channel_unreachable(tmp_db, channel="slack", tool_name="shell")

        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE kind = 'incident'")
        assert rows == []
