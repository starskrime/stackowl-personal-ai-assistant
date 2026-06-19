"""Dispatch test — /audit and /audit export are wired through CommandRegistry."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from stackowl.config.settings import Settings
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

# ---------------------------------------------------------------------------
# Fake logger for /audit tail tests (no disk required)
# ---------------------------------------------------------------------------


class _FakeAuditLogger:
    def tail(self, n: int) -> list:
        return []

    def verify_chain(self) -> tuple:
        return (True, None)


# ---------------------------------------------------------------------------
# Real AuditLogger factory for /audit export tests
# ---------------------------------------------------------------------------


def _make_real_logger(db_path: Path):  # type: ignore[no-untyped-def]
    """Build a real AuditLogger with a seeded audit_log table."""
    from stackowl.audit.logger import AuditLogger

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type   TEXT    NOT NULL,
            actor        TEXT    NOT NULL,
            target       TEXT,
            timestamp    REAL    NOT NULL,
            details      TEXT    NOT NULL DEFAULT '{}',
            integrity_hash TEXT  NOT NULL DEFAULT '',
            chain_version TEXT
        );
        INSERT INTO audit_log (event_type, actor, timestamp, details, integrity_hash)
        VALUES ('test_event', 'tester', 1234567890.0, '{}', '');
        """
    )
    conn.commit()
    conn.close()
    return AuditLogger(db_path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


# ---------------------------------------------------------------------------
# /audit tail tests (existing, unchanged behaviour)
# ---------------------------------------------------------------------------


async def test_audit_chain_intact() -> None:
    deps = CommandDeps(audit_logger=_FakeAuditLogger())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("audit", "", make_state())
    assert "Chain intact" in result


async def test_audit_not_configured_when_logger_none() -> None:
    deps = CommandDeps(audit_logger=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("audit", "", make_state())
    assert "not configured" in result


async def test_audit_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("audit", "", make_state())


# ---------------------------------------------------------------------------
# /audit export subcommand tests
# ---------------------------------------------------------------------------


async def test_audit_export_writes_json_and_sig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dispatch('audit', 'export --output <path>') writes JSON + .sig when key configured."""
    logger = _make_real_logger(tmp_path / "audit.db")
    out_path = tmp_path / "export.json"

    monkeypatch.setenv("STACKOWL_GOVERNANCE__AUDIT_EXPORT_KEY", "test-signing-key")
    deps = CommandDeps(audit_logger=logger, settings=Settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "audit", f"export --output {out_path}", make_state()
    )

    # Message confirms success
    assert "Rows exported" in result
    assert str(out_path) in result

    # Files actually exist
    assert out_path.exists(), "JSON export file must exist"
    sig_path = out_path.with_suffix(out_path.suffix + ".sig")
    assert sig_path.exists(), ".sig file must exist"

    # JSON is valid and contains at least one row
    import json
    rows = json.loads(out_path.read_bytes())
    assert isinstance(rows, list)
    assert len(rows) >= 1

    # Signature is a valid hex digest (64 chars for SHA-256)
    sig = sig_path.read_text(encoding="utf-8").strip()
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)


async def test_audit_export_empty_key_refused(tmp_path: Path) -> None:
    """dispatch('audit', 'export') with no signing key returns honest refusal — no file written."""
    logger = _make_real_logger(tmp_path / "audit.db")
    out_path = tmp_path / "should_not_exist.json"

    # governance.audit_export_key defaults to "" — not configured; no env override
    deps = CommandDeps(audit_logger=logger, settings=Settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "audit", f"export --output {out_path}", make_state()
    )

    # Honest refusal — no file written
    assert "✗" in result
    assert "signing key" in result.lower() or "key" in result.lower()
    assert not out_path.exists(), "No export file should be written when key is empty"


async def test_audit_export_not_configured_when_logger_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dispatch('audit', 'export ...') with no logger returns not-configured."""
    monkeypatch.setenv("STACKOWL_GOVERNANCE__AUDIT_EXPORT_KEY", "some-key")
    deps = CommandDeps(audit_logger=None, settings=Settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch("audit", "export", make_state())

    assert "not configured" in result
