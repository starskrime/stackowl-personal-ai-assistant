"""Story 3.1 + 3.4 tests — ConfigSection registry, /config, /help, /cost, /tier."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from pydantic import BaseModel, ConfigDict

from stackowl.commands.config_command import ConfigCommand
from stackowl.commands.config_section import (
    ConfigSection,
    ConfigSectionRegistry,
    register_section,
)
from stackowl.commands.cost_command import CostCommand
from stackowl.commands.help_command import HelpCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.tier_command import (
    TierCommand,
    get_session_tier,
    reset_session_tiers,
)
from stackowl.events.bus import EventBus
from stackowl.pipeline.state import PipelineState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(session: str = "sess-1") -> PipelineState:
    return PipelineState(
        trace_id="trace-1",
        session_id=session,
        input_text="hello",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


class _StubSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    knob: int = 7


class _StubSection(ConfigSection):
    @property
    def section_name(self) -> str:
        return "_stub_test"

    def schema(self) -> type[BaseModel]:
        return _StubSchema

    def defaults(self) -> BaseModel:
        return _StubSchema()


# ---------------------------------------------------------------------------
# ConfigSectionRegistry
# ---------------------------------------------------------------------------


class TestConfigSectionRegistry:
    def setup_method(self) -> None:
        ConfigSectionRegistry.reset()

    def teardown_method(self) -> None:
        ConfigSectionRegistry.reset()

    def test_instance_returns_singleton(self) -> None:
        a = ConfigSectionRegistry.instance()
        b = ConfigSectionRegistry.instance()
        assert a is b

    def test_register_and_get(self) -> None:
        section = _StubSection()
        ConfigSectionRegistry.instance().register(section)
        assert ConfigSectionRegistry.instance().get("_stub_test") is section

    def test_get_unknown_returns_none(self) -> None:
        assert ConfigSectionRegistry.instance().get("nope") is None

    def test_all_returns_sorted(self) -> None:
        class _Other(_StubSection):
            @property
            def section_name(self) -> str:
                return "_aaa_first"

        reg = ConfigSectionRegistry.instance()
        reg.register(_StubSection())
        reg.register(_Other())
        names = [s.section_name for s in reg.all()]
        assert names == sorted(names)

    def test_register_section_helper(self) -> None:
        s = register_section(_StubSection())
        assert ConfigSectionRegistry.instance().get("_stub_test") is s


# ---------------------------------------------------------------------------
# HelpCommand
# ---------------------------------------------------------------------------


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_lists_registered_commands(self) -> None:
        out = await HelpCommand().handle("", _state())
        assert "Available commands" in out
        # Built-in registry includes /help itself once imported
        assert "/help" in out

    @pytest.mark.asyncio
    async def test_plugin_command_appears_after_registration(self) -> None:
        from stackowl.commands.base import SlashCommand

        class _Plugin(SlashCommand):
            @property
            def command(self) -> str:
                return "plugincmd"

            @property
            def description(self) -> str:
                return "Plugin command appears via /help."

            async def handle(self, args: str, state: PipelineState) -> str:
                return "ok"

        CommandRegistry.instance().register(_Plugin())
        out = await HelpCommand().handle("", _state())
        assert "/plugincmd" in out


# ---------------------------------------------------------------------------
# TierCommand
# ---------------------------------------------------------------------------


class TestTierCommand:
    def setup_method(self) -> None:
        reset_session_tiers()

    def teardown_method(self) -> None:
        reset_session_tiers()

    @pytest.mark.asyncio
    async def test_unknown_tier_returns_error(self) -> None:
        out = await TierCommand().handle("ultra", _state())
        assert out.startswith("✗")
        assert get_session_tier("sess-1") is None

    @pytest.mark.asyncio
    async def test_valid_tier_stored(self) -> None:
        out = await TierCommand().handle("powerful", _state("sess-A"))
        assert "powerful" in out
        assert get_session_tier("sess-A") == "powerful"

    @pytest.mark.asyncio
    async def test_session_isolation(self) -> None:
        await TierCommand().handle("fast", _state("sess-X"))
        await TierCommand().handle("local", _state("sess-Y"))
        assert get_session_tier("sess-X") == "fast"
        assert get_session_tier("sess-Y") == "local"

    @pytest.mark.asyncio
    async def test_no_args_shows_current(self) -> None:
        await TierCommand().handle("standard", _state("sess-Z"))
        out = await TierCommand().handle("", _state("sess-Z"))
        assert "standard" in out


# ---------------------------------------------------------------------------
# ConfigCommand (unit, isolated yaml via STACKOWL_CONFIG_FILE)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "settings_watch": False,
                "test_mode": True,
                "budget": {"daily_limit_usd": 5.0},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


class TestConfigCommand:
    @pytest.mark.asyncio
    async def test_no_subcommand_returns_usage(self) -> None:
        out = await ConfigCommand().handle("", _state())
        assert "Usage:" in out
        assert "/config" in out

    @pytest.mark.asyncio
    async def test_unknown_subcommand_returns_usage(self) -> None:
        out = await ConfigCommand().handle("frobnicate", _state())
        assert "Usage:" in out

    @pytest.mark.asyncio
    async def test_list_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(tmp_path / "absent.yaml"))
        out = await ConfigCommand().handle("list", _state())
        assert "No stackowl.yaml" in out

    @pytest.mark.asyncio
    async def test_list_present(self, tmp_yaml: Path) -> None:
        out = await ConfigCommand().handle("list", _state())
        assert "budget.daily_limit_usd" in out
        assert "5.0" in out

    @pytest.mark.asyncio
    async def test_get_present(self, tmp_yaml: Path) -> None:
        out = await ConfigCommand().handle("get budget.daily_limit_usd", _state())
        assert "5.0" in out

    @pytest.mark.asyncio
    async def test_get_missing(self, tmp_yaml: Path) -> None:
        out = await ConfigCommand().handle("get budget.no_such_key", _state())
        assert "(not set)" in out

    @pytest.mark.asyncio
    async def test_set_writes_yaml(self, tmp_yaml: Path) -> None:
        cmd = ConfigCommand(event_bus=EventBus())
        out = await cmd.handle("set budget.daily_limit_usd 12.50", _state())
        assert "✓" in out
        reloaded: dict[str, Any] = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
        assert reloaded["budget"]["daily_limit_usd"] == 12.5

    @pytest.mark.asyncio
    async def test_set_unknown_key(self, tmp_yaml: Path) -> None:
        out = await ConfigCommand().handle("set bogus.path 1", _state())
        assert "Unknown setting" in out

    @pytest.mark.asyncio
    async def test_set_orchestrator_marks_restart(self, tmp_yaml: Path) -> None:
        out = await ConfigCommand().handle("set orchestrator.backend asyncio", _state())
        assert "restart required" in out

    @pytest.mark.asyncio
    async def test_set_sensitive_blocked(self, tmp_yaml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Patch a known field to be sensitive for this test only.
        from stackowl.config.settings import BudgetSettings

        field = BudgetSettings.model_fields["daily_limit_usd"]
        original_extra = field.json_schema_extra
        field.json_schema_extra = {"sensitive": True}
        try:
            out = await ConfigCommand().handle("set budget.daily_limit_usd 9.0", _state())
            assert "sensitive" in out.lower()
            assert "SecretResolver" in out or "keychain" in out
        finally:
            field.json_schema_extra = original_extra

    @pytest.mark.asyncio
    async def test_reset_removes_key(self, tmp_yaml: Path) -> None:
        out = await ConfigCommand().handle("reset budget.daily_limit_usd", _state())
        assert "✓" in out
        reloaded: dict[str, Any] = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
        assert "daily_limit_usd" not in reloaded.get("budget", {})

    @pytest.mark.asyncio
    async def test_export_dumps_file(self, tmp_yaml: Path) -> None:
        out = await ConfigCommand().handle("export", _state())
        assert "budget" in out
        assert "daily_limit_usd" in out

    @pytest.mark.asyncio
    async def test_4point_logging_on_list(self, tmp_yaml: Path, capture_logs: list[dict[str, Any]]) -> None:
        import logging as _logging

        _logging.getLogger("stackowl").setLevel(_logging.DEBUG)
        await ConfigCommand().handle("list", _state())
        msgs = [r["msg"] for r in capture_logs]
        assert any("config.handle: entry" in m for m in msgs)
        assert any("config.list: entry" in m for m in msgs)
        assert any("config.handle: exit" in m for m in msgs)


# ---------------------------------------------------------------------------
# CostCommand (mock the DB-dependent path)
# ---------------------------------------------------------------------------


class TestCostCommand:
    @pytest.mark.asyncio
    async def test_no_db_returns_graceful_message(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("STACKOWL_DATA_DIR", str(tmp_path / "no_such_dir"))
        with patch(
            "stackowl.providers.cost_tracker.CostTracker.daily_total",
            AsyncMock(side_effect=RuntimeError("no table cost_records")),
        ):
            out = await CostCommand().handle("", _state())
        assert out == "No cost data yet"

    @pytest.mark.asyncio
    async def test_privacy_without_yes_does_not_delete(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("STACKOWL_DATA_DIR", str(tmp_path))
        delete_mock = AsyncMock()
        with (
            patch("stackowl.db.pool.DbPool.execute", delete_mock),
            patch("stackowl.db.pool.DbPool.open", AsyncMock()),
            patch("stackowl.db.pool.DbPool.close", AsyncMock()),
        ):
            out = await CostCommand().handle("privacy", _state())
        assert "YES" in out
        delete_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_privacy_with_yes_deletes(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("STACKOWL_DATA_DIR", str(tmp_path))
        delete_mock = AsyncMock()
        with (
            patch("stackowl.db.pool.DbPool.execute", delete_mock),
            patch("stackowl.db.pool.DbPool.open", AsyncMock()),
            patch("stackowl.db.pool.DbPool.close", AsyncMock()),
        ):
            out = await CostCommand().handle("privacy YES", _state())
        assert "✓" in out
        delete_mock.assert_awaited_once()
        sql = delete_mock.await_args.args[0]
        assert "DELETE FROM cost_records" in sql

    @pytest.mark.asyncio
    async def test_unknown_subcommand_returns_usage(self, tmp_path: Path) -> None:
        out = await CostCommand().handle("frobby", _state())
        assert "Usage:" in out
