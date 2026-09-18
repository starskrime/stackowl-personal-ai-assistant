"""Story 2.8 -- ``ConsentPolicy.request()`` records a ``consent.decided``
journal event (and a ``consent_decision_records`` row) alongside its
existing, UNCHANGED synchronous ``audit_log`` write, when a ``db_pool`` is
wired.
"""

from __future__ import annotations

import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.tools.consent import ConsentPolicy, ConsentRequest, ConsentScope, TrustTier

pytestmark = pytest.mark.asyncio


class _RecordingPrompter:
    """Returns a fixed scope; records whether/what it was asked."""

    def __init__(self, scope: ConsentScope = ConsentScope.ONCE) -> None:
        self._scope = scope
        self.asked = False

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        self.asked = True
        return self._scope


class _RecordingAuditLogger:
    """AuditLogger-shaped stub (.append) -- proves the EXISTING sync audit
    write still lands alongside the new journal row (regression guard)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def append(self, event_type: str, *, actor: str, target: str, details: dict) -> None:
        self.calls.append({
            "event_type": event_type, "actor": actor, "target": target, "details": details,
        })


async def _journal_rows(tmp_db: DbPool, tool_name: str) -> list[dict]:
    return await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'consent.decided' AND target_id = ?",
        (tool_name,),
    )


class TestTierNeverDeniedIsJournaled:
    async def test_deny_lands_in_the_journal_and_the_audit_log(self, tmp_db: DbPool) -> None:
        audit = _RecordingAuditLogger()
        policy = ConsentPolicy(
            prompter=_RecordingPrompter(),
            audit_logger=audit,
            tiers={"blocked-tool": TrustTier.NEVER},
            db_pool=tmp_db,
        )

        allowed = await policy.request(
            tool_name="blocked-tool", channel="cli", session_key="s1",
        )

        assert allowed is False
        # Regression guard: the existing sync audit write is UNTOUCHED.
        assert len(audit.calls) == 1
        assert audit.calls[0]["event_type"] == "consent.decision"
        assert audit.calls[0]["details"]["decision"] == "deny"
        assert audit.calls[0]["details"]["reason"] == "tier_never"

        rows = await _journal_rows(tmp_db, "blocked-tool")
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "failed"
        attrs = json.loads(row["attrs"])
        assert attrs["decision"] == "deny"
        assert attrs["reason"] == "tier_never"

        consent_rows = await tmp_db.fetch_all(
            "SELECT decision FROM consent_decision_records WHERE tool_name = ?",
            ("blocked-tool",),
        )
        assert len(consent_rows) == 1
        assert consent_rows[0]["decision"] == "deny"


class TestUserApprovedIsJournaled:
    async def test_allow_lands_in_the_journal_and_the_audit_log(self, tmp_db: DbPool) -> None:
        audit = _RecordingAuditLogger()
        prompter = _RecordingPrompter(scope=ConsentScope.ONCE)
        policy = ConsentPolicy(
            prompter=prompter, audit_logger=audit, db_pool=tmp_db,
        )

        allowed = await policy.request(
            tool_name="shell", channel="telegram", session_key="s2", summary="run ls",
        )

        assert allowed is True
        assert prompter.asked is True
        assert audit.calls[0]["details"]["decision"] == "allow"

        rows = await _journal_rows(tmp_db, "shell")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "ok"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["decision"] == "allow"
        assert attrs["channel"] == "telegram"


class TestNoDbPoolWiredIsUnaffected:
    async def test_decision_and_audit_still_work_with_nothing_journaled(self) -> None:
        """The fallback `ConsentPolicy()` shape (e.g. `registry.py`'s bare
        default) -- no `db_pool` -- must decide and audit exactly as before,
        with no journal row and no crash."""
        audit = _RecordingAuditLogger()
        policy = ConsentPolicy(
            prompter=_RecordingPrompter(scope=ConsentScope.ONCE), audit_logger=audit,
        )

        allowed = await policy.request(tool_name="shell", channel="cli", session_key="s3")

        assert allowed is True
        assert len(audit.calls) == 1


class TestTheJournalWriteNeverBlocksTheDecision:
    async def test_a_journal_failure_still_returns_the_correct_allowed_value(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import consent_events as consent_events_module

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal failure")

        # Patches the INNER `journal.record()` call so
        # `record_consent_decision`'s OWN swallow (B5) is what protects
        # `_finalize` end-to-end -- the same failure shape
        # `test_consent_events.py`'s atomicity test drives directly.
        monkeypatch.setattr(consent_events_module, "journal_record", _boom)

        audit = _RecordingAuditLogger()
        policy = ConsentPolicy(
            prompter=_RecordingPrompter(scope=ConsentScope.ONCE),
            audit_logger=audit, db_pool=tmp_db,
        )

        allowed = await policy.request(tool_name="shell", channel="cli", session_key="s4")

        assert allowed is True
        assert len(audit.calls) == 1
