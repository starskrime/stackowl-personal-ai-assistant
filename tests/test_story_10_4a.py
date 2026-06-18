"""Story 10.4a — AuditLogger and AuditCommand tests."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.audit.logger import AuditLogger
from stackowl.commands.audit import AuditCommand


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Return a temp db path with audit_log table (WITH triggers for normal tests)."""
    p = tmp_path / "audit_test.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type   TEXT    NOT NULL,
            actor        TEXT    NOT NULL,
            target       TEXT,
            timestamp    REAL    NOT NULL,
            details      TEXT    NOT NULL DEFAULT '{}',
            integrity_hash TEXT  NOT NULL DEFAULT ''
        );
        CREATE TRIGGER IF NOT EXISTS audit_log_no_update
            BEFORE UPDATE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
            BEFORE DELETE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END;
        """
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture()
def notrigger_db_path(tmp_path: Path) -> Path:
    """Return a temp db path with audit_log table but NO triggers (for tampering tests)."""
    p = tmp_path / "audit_notrigger.db"
    conn = sqlite3.connect(p)
    conn.execute(
        """
        CREATE TABLE audit_log (
            audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type   TEXT    NOT NULL,
            actor        TEXT    NOT NULL,
            target       TEXT,
            timestamp    REAL    NOT NULL,
            details      TEXT    NOT NULL DEFAULT '{}',
            integrity_hash TEXT  NOT NULL DEFAULT ''
        )
        """
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture()
def audit_logger(db_path: Path) -> AuditLogger:
    return AuditLogger(db_path)


@pytest.fixture()
def pipeline_state() -> MagicMock:
    state = MagicMock()
    state.session_id = "test-session"
    return state


# ---------------------------------------------------------------------------
# AuditLogger tests
# ---------------------------------------------------------------------------


def test_audit_logger_append_creates_row(audit_logger: AuditLogger) -> None:
    """append() creates one row retrievable via tail(1)."""
    audit_logger.append("user.login", "cli", None, {"ip": "127.0.0.1"})
    rows = audit_logger.tail(1)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "user.login"
    assert rows[0]["actor"] == "cli"
    assert rows[0]["target"] is None


def test_audit_logger_tail_returns_n_rows(audit_logger: AuditLogger) -> None:
    """tail(n) returns exactly n rows when more than n exist."""
    for i in range(5):
        audit_logger.append("test.event", "cli", f"target-{i}", {"i": i})
    rows = audit_logger.tail(3)
    assert len(rows) == 3


def test_audit_logger_integrity_hash_is_set(audit_logger: AuditLogger) -> None:
    """After append(), the row has a non-empty integrity_hash."""
    audit_logger.append("boot", "system", None, {})
    rows = audit_logger.tail(1)
    assert rows[0]["integrity_hash"] != ""
    assert len(rows[0]["integrity_hash"]) == 64  # SHA-256 hex


def test_audit_logger_verify_chain_intact_on_fresh_data(audit_logger: AuditLogger) -> None:
    """verify_chain() returns (True, None) for consecutively appended rows."""
    audit_logger.append("event.a", "alice", "resource-1", {"action": "read"})
    audit_logger.append("event.b", "bob", "resource-2", {"action": "write"})
    audit_logger.append("event.c", "charlie", None, {})
    ok, broken = audit_logger.verify_chain()
    assert ok is True
    assert broken is None


def test_audit_logger_verify_chain_detects_tampering(notrigger_db_path: Path) -> None:
    """verify_chain() returns (False, audit_id) when a row has a wrong hash."""
    # Insert a row with a deliberately wrong integrity_hash (no triggers on this db)
    conn = sqlite3.connect(notrigger_db_path)
    conn.execute(
        "INSERT INTO audit_log (event_type, actor, target, timestamp, details, integrity_hash) "
        "VALUES ('test', 'cli', NULL, ?, '{}', 'wronghash')",
        (time.time(),),
    )
    conn.commit()
    conn.close()

    logger = AuditLogger(notrigger_db_path)
    ok, broken = logger.verify_chain()
    assert ok is False
    assert broken is not None


def test_audit_logger_tail_empty_db(audit_logger: AuditLogger) -> None:
    """tail() on a fresh db returns an empty list."""
    rows = audit_logger.tail()
    assert rows == []


# ---------------------------------------------------------------------------
# AuditCommand tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_audit_logger() -> MagicMock:
    m = MagicMock(spec=AuditLogger)
    m.tail.return_value = [
        {
            "audit_id": 1,
            "event_type": "user.login",
            "actor": "cli",
            "target": None,
            "timestamp": 1700000000.0,
            "details": '{"ip": "127.0.0.1"}',
            "integrity_hash": "abc" * 20,
        }
    ]
    m.verify_chain.return_value = (True, None)
    return m


@pytest.fixture()
def audit_command(mock_audit_logger: MagicMock) -> AuditCommand:
    return AuditCommand(mock_audit_logger)


@pytest.mark.asyncio
async def test_audit_command_renders_chain_intact(
    audit_command: AuditCommand,
    pipeline_state: MagicMock,
) -> None:
    """handle() includes 'Chain intact' when verify_chain() returns (True, None)."""
    result = await audit_command.handle("", pipeline_state)
    assert "Chain intact" in result


@pytest.mark.asyncio
async def test_audit_command_renders_broken_chain(
    mock_audit_logger: MagicMock,
    pipeline_state: MagicMock,
) -> None:
    """handle() includes 'Chain broken at record 5' when verify_chain() returns (False, 5)."""
    mock_audit_logger.verify_chain.return_value = (False, 5)
    cmd = AuditCommand(mock_audit_logger)
    result = await cmd.handle("", pipeline_state)
    assert "Chain broken at record 5" in result


@pytest.mark.asyncio
async def test_audit_command_formats_table_with_columns(
    audit_command: AuditCommand,
    pipeline_state: MagicMock,
) -> None:
    """handle() output contains column header labels 'event_type' and 'actor'."""
    result = await audit_command.handle("", pipeline_state)
    assert "event_type" in result
    assert "actor" in result


@pytest.mark.asyncio
async def test_audit_command_tail_is_50(
    audit_command: AuditCommand,
    mock_audit_logger: MagicMock,
    pipeline_state: MagicMock,
) -> None:
    """handle() calls tail(50) exactly once."""
    await audit_command.handle("", pipeline_state)
    mock_audit_logger.tail.assert_called_once_with(50)
