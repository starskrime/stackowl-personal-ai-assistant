"""Regression — consent decisions must be durably audited.

Live log surfaced ``ERROR [consent] policy.request: audit append failed`` from
``consent.py`` ``_finalize``: the decision itself succeeded but the audit write
raised. Root cause: ``AuditLogger.append`` was failing with
``sqlite3.OperationalError: no such table: audit_log`` against a DB whose audit
schema had not been provisioned, because ``AuditLogger`` never ensured its own
table existed. This test wires a *real* ``AuditLogger`` (not a fake) at a fresh
DB path through a real consent decision and asserts the row actually persisted.
"""

from __future__ import annotations

from pathlib import Path

from stackowl.audit.logger import AuditLogger
from stackowl.tools.consent import ConsentPolicy, ConsentScope


class _AllowPrompter:
    async def prompt(self, req: object) -> ConsentScope:  # noqa: ARG002
        return ConsentScope.ONCE


async def test_consent_decision_is_durably_audited_on_fresh_db(tmp_path: Path) -> None:
    """A consent decision through a real AuditLogger persists a real row.

    Reproduces the live bug: against a DB that has not had the audit migration
    applied, ``append`` raised ``no such table`` and the decision went
    un-audited. The logger must self-provision its schema so the append
    succeeds.
    """
    db_path = tmp_path / "fresh.db"  # no migrations have run against this DB
    audit = AuditLogger(db_path)
    policy = ConsentPolicy(prompter=_AllowPrompter(), audit_logger=audit)

    allowed = await policy.request(tool_name="shell", channel="cli", session_id="s1")

    assert allowed is True
    rows = audit.tail(10)
    decisions = [r for r in rows if r["event_type"] == "consent.decision"]
    assert len(decisions) == 1, f"consent decision was not audited: {rows!r}"
    row = decisions[0]
    assert row["actor"] == "s1"
    assert row["target"] == "shell"


def test_audit_logger_append_succeeds_on_unmigrated_db(tmp_path: Path) -> None:
    """AuditLogger.append must not raise on a DB that lacks the audit table."""
    audit = AuditLogger(tmp_path / "bare.db")
    audit.append(
        "consent.decision",
        actor="s1",
        target="shell",
        details={"decision": "allow"},
    )
    rows = audit.tail(5)
    assert any(r["event_type"] == "consent.decision" for r in rows)
