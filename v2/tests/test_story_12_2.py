"""Tests for Story 12.2: tool severity gate, setup flows, AI Act disclosure, onboarding table."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate


# ---------------------------------------------------------------------------
# Minimal concrete Tool implementations for testing
# ---------------------------------------------------------------------------


class _ReadTool(Tool):
    @property
    def name(self) -> str:
        return "test_read"

    @property
    def description(self) -> str:
        return "A read-only test tool."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=0.0)


class _WriteToolCustomManifest(Tool):
    """Tool that overrides manifest to declare write severity."""

    @property
    def name(self) -> str:
        return "test_write"

    @property
    def description(self) -> str:
        return "A write-severity test tool."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="written", duration_ms=0.0)


class _ConsequentialTool(Tool):
    """Tool that overrides manifest to declare consequential severity."""

    @property
    def name(self) -> str:
        return "test_consequential"

    @property
    def description(self) -> str:
        return "A consequential test tool."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="done", duration_ms=0.0)


# ---------------------------------------------------------------------------
# ToolManifest tests
# ---------------------------------------------------------------------------


def test_tool_manifest_action_severity_default() -> None:
    """ToolManifest.action_severity defaults to 'read'."""
    manifest = ToolManifest(
        name="example",
        description="An example tool.",
        parameters={"type": "object", "properties": {}},
    )
    assert manifest.action_severity == "read"


def test_tool_manifest_action_severity_explicit_values() -> None:
    """ToolManifest accepts all three severity literals."""
    for severity in ("read", "write", "consequential"):
        m = ToolManifest(
            name="t",
            description="d",
            parameters={},
            action_severity=severity,  # type: ignore[arg-type]
        )
        assert m.action_severity == severity


def test_tool_default_manifest_returns_read_severity() -> None:
    """Tool.manifest property returns a ToolManifest with action_severity='read' by default."""
    tool = _ReadTool()
    assert tool.manifest.action_severity == "read"
    assert tool.manifest.name == "test_read"


def test_tool_custom_manifest_severity() -> None:
    """A Tool that overrides manifest() can declare consequential severity."""
    tool = _ConsequentialTool()
    assert tool.manifest.action_severity == "consequential"


# ---------------------------------------------------------------------------
# ConsequentialActionGate tests
# ---------------------------------------------------------------------------


async def test_gate_check_returns_true_for_read_tool_without_calling_confirm_fn() -> None:
    """Gate must not call confirm_fn for non-consequential tools."""
    confirm_fn = MagicMock(return_value=True)
    gate = ConsequentialActionGate(confirm_fn=confirm_fn)
    result = await gate.check(_ReadTool())
    assert result is True
    confirm_fn.assert_not_called()


async def test_gate_check_calls_confirm_fn_for_consequential_tool() -> None:
    """Gate calls confirm_fn when tool has consequential severity."""
    confirm_fn = MagicMock(return_value=True)
    gate = ConsequentialActionGate(confirm_fn=confirm_fn)
    tool = _ConsequentialTool()
    result = await gate.check(tool)
    assert result is True
    confirm_fn.assert_called_once_with(tool.name)


async def test_gate_check_returns_false_when_confirm_fn_returns_false() -> None:
    """Gate returns False when the confirm_fn denies execution."""
    gate = ConsequentialActionGate(confirm_fn=lambda _name: False)
    assert await gate.check(_ConsequentialTool()) is False


async def test_gate_check_returns_true_for_write_tool_without_calling_confirm_fn() -> None:
    """write severity is not consequential — gate allows without prompting."""
    confirm_fn = MagicMock(return_value=False)
    gate = ConsequentialActionGate(confirm_fn=confirm_fn)
    assert await gate.check(_WriteToolCustomManifest()) is True
    confirm_fn.assert_not_called()


async def test_gate_default_construction_fails_closed() -> None:
    """Default construction (no policy, no confirm_fn) denies consequential actions."""
    gate = ConsequentialActionGate()  # FailClosedPrompter
    assert await gate.check(_ConsequentialTool()) is False


# ---------------------------------------------------------------------------
# AiActDisclosure tests
# ---------------------------------------------------------------------------


def test_ai_act_disclosure_get_text_returns_non_empty() -> None:
    """get_text() returns a non-empty string for the default English locale."""
    from stackowl.setup.disclosure import AiActDisclosure

    disclosure = AiActDisclosure()
    text = disclosure.get_text()
    assert isinstance(text, str)
    assert len(text) > 0


def test_ai_act_disclosure_get_text_accepts_lang_param() -> None:
    """get_text(lang='de') returns German text without crashing."""
    from stackowl.setup.disclosure import AiActDisclosure

    disclosure = AiActDisclosure()
    text_de = disclosure.get_text(lang="de")
    text_en = disclosure.get_text(lang="en")
    assert isinstance(text_de, str)
    assert len(text_de) > 0
    # German and English should differ
    assert text_de != text_en


def test_ai_act_disclosure_was_shown_returns_false_before_mark() -> None:
    """was_shown_this_session() returns False before mark_shown() is called."""
    from stackowl.setup.disclosure import AiActDisclosure

    disclosure = AiActDisclosure()
    assert disclosure.was_shown_this_session("session-abc") is False


def test_ai_act_disclosure_was_shown_returns_true_after_mark() -> None:
    """was_shown_this_session() returns True after mark_shown() is called."""
    from stackowl.setup.disclosure import AiActDisclosure

    disclosure = AiActDisclosure()
    disclosure.mark_shown("session-xyz")
    assert disclosure.was_shown_this_session("session-xyz") is True


def test_ai_act_disclosure_sessions_are_independent() -> None:
    """Marking one session does not affect other sessions."""
    from stackowl.setup.disclosure import AiActDisclosure

    disclosure = AiActDisclosure()
    disclosure.mark_shown("session-1")
    assert disclosure.was_shown_this_session("session-1") is True
    assert disclosure.was_shown_this_session("session-2") is False


# ---------------------------------------------------------------------------
# OnboardingTable tests (using a real temp SQLite DB)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def onboarding_db(tmp_path: Path) -> Any:
    """DbPool with the onboarding_events table created."""
    from stackowl.db.migrations.runner import MigrationRunner
    from stackowl.db.pool import DbPool

    db_path = tmp_path / "onboarding_test.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def test_onboarding_has_event_returns_false_if_no_row(onboarding_db: Any) -> None:
    """has_event() returns False when the event has not been recorded."""
    from stackowl.setup.onboarding_table import OnboardingTable

    result = await OnboardingTable.has_event(onboarding_db, "welcome_shown")
    assert result is False


async def test_onboarding_record_then_has_event(onboarding_db: Any) -> None:
    """record_event() then has_event() returns True."""
    from stackowl.setup.onboarding_table import OnboardingTable

    await OnboardingTable.record_event(onboarding_db, "welcome_shown")
    assert await OnboardingTable.has_event(onboarding_db, "welcome_shown") is True


async def test_onboarding_record_duplicate_is_noop(onboarding_db: Any) -> None:
    """Recording the same event twice does not raise."""
    from stackowl.setup.onboarding_table import OnboardingTable

    await OnboardingTable.record_event(onboarding_db, "provider_configured")
    await OnboardingTable.record_event(onboarding_db, "provider_configured")
    assert await OnboardingTable.has_event(onboarding_db, "provider_configured") is True


# ---------------------------------------------------------------------------
# Migration 0025 existence test
# ---------------------------------------------------------------------------


def test_migration_0025_exists() -> None:
    """Migration file 0025_onboarding_events.sql must exist."""
    migrations_dir = (
        Path(__file__).parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    candidates = sorted(migrations_dir.glob("0025_*.sql"))
    assert len(candidates) == 1, f"Expected exactly one 0025_*.sql, found: {candidates}"


def test_migration_0025_creates_onboarding_events_table(tmp_path: Path) -> None:
    """Running all migrations creates the onboarding_events table."""
    from stackowl.db.migrations.runner import MigrationRunner

    db_path = tmp_path / "mig_test.db"
    MigrationRunner(db_path=db_path).run()

    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "onboarding_events" in tables, f"onboarding_events not found in {tables}"


# ---------------------------------------------------------------------------
# configs/demo/owls.yaml exists
# ---------------------------------------------------------------------------


def test_demo_owls_yaml_exists() -> None:
    """configs/demo/owls.yaml must exist so --demo mode has owl definitions."""
    owls_yaml = (
        Path(__file__).parent.parent / "configs" / "demo" / "owls.yaml"
    )
    assert owls_yaml.exists(), f"Missing: {owls_yaml}"
    content = owls_yaml.read_text(encoding="utf-8")
    assert "owls" in content, "owls.yaml must contain an 'owls' key"
