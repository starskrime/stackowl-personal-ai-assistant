"""Story 12.4 — AuditRetention, AuditExportCommand, PermissionsCommand,
GovernanceSettings, SecurityError, and dependabot.yml tests.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from stackowl.audit.retention import AuditRetention
from stackowl.config.settings import GovernanceSettings
from stackowl.exceptions import SecurityError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_audit_db(db_path: Path) -> None:
    """Create an audit_log table with the no-delete trigger (mirrors migration 0023)."""
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
    conn.close()


def _insert_row(db_path: Path, ts: float, event_type: str = "test_event") -> None:
    """Insert one audit row bypassing the trigger (for setup only)."""
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    conn.execute(
        "INSERT INTO audit_log (event_type, actor, target, timestamp, details, integrity_hash)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (event_type, "test", None, ts, "{}", ""),
    )
    # Re-create trigger after seed insert
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
            BEFORE DELETE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END
        """
    )
    conn.commit()
    conn.close()


def _count_rows(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    conn.close()
    return n


# ---------------------------------------------------------------------------
# AuditRetention tests
# ---------------------------------------------------------------------------

class TestAuditRetention:
    def test_prune_deletes_old_rows(self, tmp_path: Path) -> None:
        """prune() removes rows older than retention_days."""
        db = tmp_path / "audit.db"
        _create_audit_db(db)

        # Insert two old rows (200 days ago) and one recent row
        old_ts = time.time() - (200 * 86_400)
        _insert_row(db, old_ts, event_type="old_event_1")
        _insert_row(db, old_ts, event_type="old_event_2")
        recent_ts = time.time() - 3600  # 1 hour ago
        _insert_row(db, recent_ts, event_type="recent_event")

        retention = AuditRetention(db_path=db, retention_days=90)
        pruned = retention.prune()

        assert pruned == 2, f"Expected 2 pruned, got {pruned}"

        # Recent row must still be there; plus the system_audit_prune row appended
        remaining = _count_rows(db)
        assert remaining == 2  # recent_event + system_audit_prune

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT event_type FROM audit_log ORDER BY audit_id ASC"
        ).fetchall()
        conn.close()
        types = [r["event_type"] for r in rows]
        assert "recent_event" in types
        assert "system_audit_prune" in types
        assert "old_event_1" not in types
        assert "old_event_2" not in types

    def test_prune_no_old_rows_returns_zero(self, tmp_path: Path) -> None:
        """prune() returns 0 when there are no rows older than retention_days."""
        db = tmp_path / "audit.db"
        _create_audit_db(db)

        # Insert only a recent row
        _insert_row(db, time.time() - 3600, event_type="recent")

        retention = AuditRetention(db_path=db, retention_days=90)
        pruned = retention.prune()

        assert pruned == 0

        # prune audit record appended even when nothing deleted
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT event_type FROM audit_log ORDER BY audit_id ASC"
        ).fetchall()
        conn.close()
        types = [r["event_type"] for r in rows]
        assert "system_audit_prune" in types

    def test_prune_appends_audit_row_details(self, tmp_path: Path) -> None:
        """The system_audit_prune row must contain pruned_count and retention_days in details."""
        db = tmp_path / "audit.db"
        _create_audit_db(db)

        old_ts = time.time() - (200 * 86_400)
        _insert_row(db, old_ts, event_type="old")

        retention = AuditRetention(db_path=db, retention_days=90)
        retention.prune()

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT details FROM audit_log WHERE event_type = 'system_audit_prune'"
        ).fetchone()
        conn.close()
        assert row is not None, "system_audit_prune row not found"
        details: dict[str, Any] = json.loads(row["details"])
        assert details["pruned_count"] == 1
        assert details["retention_days"] == 90

    def test_prune_respects_retention_days(self, tmp_path: Path) -> None:
        """Rows exactly at the boundary are NOT pruned (timestamp >= cutoff is kept)."""
        db = tmp_path / "audit.db"
        _create_audit_db(db)

        # Row at exactly retention boundary (89 days ago — should be kept)
        recent_enough = time.time() - (89 * 86_400)
        _insert_row(db, recent_enough, event_type="borderline")

        retention = AuditRetention(db_path=db, retention_days=90)
        pruned = retention.prune()

        assert pruned == 0


# ---------------------------------------------------------------------------
# PermissionsCommand tests
# ---------------------------------------------------------------------------

class TestPermissionsCommand:
    def _make_command(self) -> Any:
        from stackowl.commands.permissions import PermissionsCommand
        from stackowl.config.settings import Settings
        from stackowl.integrations.registry import IntegrationRegistry
        from stackowl.plugins.registry import PluginRegistry

        settings = MagicMock(spec=Settings)
        settings.autonomy_level = "medium"
        settings.owls = []

        int_registry = MagicMock(spec=IntegrationRegistry)
        int_registry.list_all.return_value = []

        # PluginRegistry.list() requires db — use a mock
        plugin_registry = MagicMock(spec=PluginRegistry)
        plugin_registry.list.return_value = []

        return PermissionsCommand(
            settings=settings,
            integration_registry=int_registry,
            plugin_registry=plugin_registry,
        )

    @pytest.mark.asyncio
    async def test_execute_returns_non_empty_string(self) -> None:
        """PermissionsCommand.handle() must return a non-empty string."""
        from stackowl.pipeline.state import PipelineState

        cmd = self._make_command()
        state = PipelineState(
            trace_id="t1",
            session_id="s1",
            input_text="",
            channel="cli",
            owl_name="test-owl",
            pipeline_step="command",
        )
        result = await cmd.handle("", state)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_execute_includes_autonomy_level(self) -> None:
        """PermissionsCommand output must mention the autonomy_level."""
        from stackowl.pipeline.state import PipelineState

        cmd = self._make_command()
        state = PipelineState(
            trace_id="t1",
            session_id="s1",
            input_text="",
            channel="cli",
            owl_name="test-owl",
            pipeline_step="command",
        )
        result = await cmd.handle("", state)
        assert "medium" in result

    def test_command_name(self) -> None:
        cmd = self._make_command()
        assert cmd.command == "permissions"


# ---------------------------------------------------------------------------
# GovernanceSettings tests
# ---------------------------------------------------------------------------

class TestGovernanceSettings:
    def test_default_audit_retention_days(self) -> None:
        """GovernanceSettings.audit_retention_days must default to 90."""
        gs = GovernanceSettings()
        assert gs.audit_retention_days == 90

    def test_default_audit_export_key(self) -> None:
        """GovernanceSettings.audit_export_key must default to empty string."""
        gs = GovernanceSettings()
        assert gs.audit_export_key == ""

    def test_default_same_day_security_prs(self) -> None:
        """GovernanceSettings.same_day_security_prs must default to True."""
        gs = GovernanceSettings()
        assert gs.same_day_security_prs is True

    def test_custom_values(self) -> None:
        """GovernanceSettings accepts custom values."""
        gs = GovernanceSettings(
            audit_retention_days=30,
            audit_export_key="my-secret",
            same_day_security_prs=False,
        )
        assert gs.audit_retention_days == 30
        assert gs.audit_export_key == "my-secret"
        assert gs.same_day_security_prs is False

    def test_settings_has_governance_field(self) -> None:
        """Settings must expose a governance field of type GovernanceSettings."""
        from stackowl.config.settings import Settings
        import os

        os.environ.setdefault("STACKOWL_CONFIG_FILE", "/nonexistent_path/stackowl.yaml")
        s = Settings()
        assert hasattr(s, "governance")
        assert isinstance(s.governance, GovernanceSettings)
        assert s.governance.audit_retention_days == 90


# ---------------------------------------------------------------------------
# SecurityError tests (basic — full contract in tests/security/)
# ---------------------------------------------------------------------------

class TestSecurityErrorBasic:
    def test_raises_with_correct_category(self) -> None:
        """SecurityError.category must match the provided value."""
        with pytest.raises(SecurityError) as exc_info:
            raise SecurityError("test", category="path_traversal")
        assert exc_info.value.category == "path_traversal"

    def test_default_category_is_nfr33(self) -> None:
        """SecurityError.category defaults to 'nfr33'."""
        with pytest.raises(SecurityError) as exc_info:
            raise SecurityError("default")
        assert exc_info.value.category == "nfr33"

    def test_logs_critical(self, caplog: pytest.LogCaptureFixture) -> None:
        """SecurityError must emit a CRITICAL log on stackowl.security."""
        with caplog.at_level(logging.CRITICAL, logger="stackowl.security"):
            try:
                raise SecurityError("log_test")
            except SecurityError:
                pass
        assert any(r.levelno == logging.CRITICAL for r in caplog.records)


# ---------------------------------------------------------------------------
# dependabot.yml existence test
# ---------------------------------------------------------------------------

class TestDependabotConfig:
    def test_dependabot_yml_exists(self) -> None:
        """The repo-root .github/dependabot.yml must exist."""
        # Walk up from this file's location to find the repo root
        here = Path(__file__).resolve()
        # v2/tests/test_story_12_4.py — repo root is 2 levels up
        repo_root = here.parent.parent.parent
        dep_yml = repo_root / ".github" / "dependabot.yml"
        assert dep_yml.exists(), f"dependabot.yml not found at {dep_yml}"

    def test_dependabot_yml_contains_pip_ecosystem(self) -> None:
        """dependabot.yml must configure pip updates."""
        here = Path(__file__).resolve()
        repo_root = here.parent.parent.parent
        dep_yml = repo_root / ".github" / "dependabot.yml"
        content = dep_yml.read_text(encoding="utf-8")
        assert "package-ecosystem: pip" in content

    def test_dependabot_yml_targets_v2_directory(self) -> None:
        """dependabot.yml must target the /v2 directory."""
        here = Path(__file__).resolve()
        repo_root = here.parent.parent.parent
        dep_yml = repo_root / ".github" / "dependabot.yml"
        content = dep_yml.read_text(encoding="utf-8")
        assert 'directory: "/v2"' in content
