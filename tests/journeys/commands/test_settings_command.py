"""Dispatch tests — /settings is wired through CommandRegistry with a live bus.

Epic C1: proves the dead-bus regression is fixed for /settings.

Anti-mock rules (enforced by test_no_mock_only_command_tests.py):
  - Drive via CommandRegistry.dispatch, never call the command method directly.
  - Do NOT construct a real EventBus — use MagicMock or a fake instead.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


@pytest.fixture()
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        yaml.dump({"test_mode": True, "providers": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


async def test_settings_reachable_via_assembly() -> None:
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("settings", "", make_state())
    assert "Usage" in result or "autonomy" in result.lower()


async def test_settings_not_found_without_registration() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("settings", "", make_state())


# ---------------------------------------------------------------------------
# Bus wiring — the production bus must fire (catches the bus=None regression)
# ---------------------------------------------------------------------------


async def test_settings_autonomy_emits_on_production_bus(tmp_yaml: Path) -> None:
    """The event_bus in CommandDeps must receive settings_changed.

    Regression gate: with Pattern-A the command was registered with
    event_bus=None, so emits were always silently skipped.
    """
    spy = MagicMock()

    deps = CommandDeps(event_bus=spy)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "settings", "autonomy high", make_state()
    )

    assert "✓" in result
    spy.emit.assert_called_once()
    emitted_event = spy.emit.call_args[0][0]
    assert emitted_event == "settings_changed"


async def test_settings_no_bus_does_not_crash(tmp_yaml: Path) -> None:
    deps = CommandDeps(event_bus=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "settings", "autonomy low", make_state()
    )
    assert "✓" in result


# ---------------------------------------------------------------------------
# Smoke — invalid args handled cleanly
# ---------------------------------------------------------------------------


async def test_settings_invalid_level_returns_error(tmp_yaml: Path) -> None:
    deps = CommandDeps(event_bus=MagicMock())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "settings", "autonomy extreme", make_state()
    )
    assert "✗" in result or "invalid" in result.lower()


async def test_settings_unknown_subcommand_returns_usage(tmp_yaml: Path) -> None:
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "settings", "foo bar", make_state()
    )
    assert "Usage" in result
