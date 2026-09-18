"""``consent.decided`` is registered correctly (Story 2.8), and
``record_consent_decision`` is atomic with its own transaction (AD-24),
no-ops with no ``db_pool``, and redacts secrets through a REAL emitter
(NFR33) -- the same proof shape ``tests/journal/test_turn_events.py`` uses
for Story 2.7's types.
"""

from __future__ import annotations

import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.journal import AttentionClass, RecordKind, classify
from stackowl.journal.consent_events import ConsentDecisionAttrs, record_consent_decision
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
