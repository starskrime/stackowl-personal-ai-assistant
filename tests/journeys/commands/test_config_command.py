"""Dispatch tests — /config is wired through CommandRegistry with a live bus.

Epic C1: proves the dead-bus regression is fixed.

Previously /config was Pattern-A (self-registered at import time with
event_bus=None), so _emit_* was a permanent no-op in production.  Now it is a
DI command: the bus passed via CommandDeps.event_bus is the one that fires.

Anti-mock rules (enforced by test_no_mock_only_command_tests.py):
  - Drive via CommandRegistry.dispatch, never call the command method directly.
  - Do NOT construct a real EventBus — use MagicMock or a fake instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from stackowl.events.bus import EventBus
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


class _SpyBus(EventBus):
    """EventBus that records every emit for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, Any]] = []

    def emit(self, event: str, payload: Any = None) -> None:
        self.events.append((event, payload))
        super().emit(event, payload)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


@pytest.fixture()
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temporary stackowl.yaml with test-safe content."""
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        yaml.dump({"test_mode": True, "providers": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


def _load(cfg: Path) -> dict[str, Any]:
    return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


async def test_config_reachable_via_assembly() -> None:
    """/config must register via DI, not only via Pattern-A self-registration."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (await CommandRegistry.instance().dispatch("config", "", make_state())).text
    assert "Usage" in result or "/config" in result


async def test_config_not_found_without_registration() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("config", "", make_state())


# ---------------------------------------------------------------------------
# Bus wiring — the production bus must fire (catches the bus=None regression)
# ---------------------------------------------------------------------------


async def test_config_set_emits_on_production_bus(tmp_yaml: Path) -> None:
    """The event_bus passed in CommandDeps must receive settings_reloaded.

    This is the regression gate for the dead-bus bug: with Pattern-A the
    command was self-registered with event_bus=None, so _emit_* was always a
    no-op regardless of what was wired at the dep level.
    """
    spy = MagicMock()

    deps = CommandDeps(event_bus=spy)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = (
        await CommandRegistry.instance().dispatch(
            "config", "set autonomy_level low", make_state()
        )
    ).text

    assert "✓" in result
    spy.emit.assert_called_once()
    emitted_event = spy.emit.call_args[0][0]
    assert emitted_event == "settings_reloaded"
    # NOTE: this gates the dead-bus regression — the event now reaches the
    # production bus (was a no-op when bus=None). The ACTUAL hot-reload is
    # driven by the ConfigWatcher re-reading the file (which emits a real
    # Settings payload the reload handler consumes), NOT by this dict payload —
    # so this asserts the emit fires, not that a reload occurred.


async def test_config_set_emits_real_settings(tmp_yaml: Path) -> None:
    """After /config set, settings_reloaded must carry a real Settings object.

    This ensures type-guarded subscribers actually apply the reloaded settings,
    not a dict which they ignore.
    """
    from stackowl.config.settings import Settings

    bus = _SpyBus()
    deps = CommandDeps(event_bus=bus)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = (
        await CommandRegistry.instance().dispatch(
            "config", "set autonomy_level high", make_state()
        )
    ).text

    assert "✓" in result
    reload_events = [p for e, p in bus.events if e == "settings_reloaded"]
    assert len(reload_events) == 1
    assert isinstance(reload_events[0], Settings)


async def test_config_set_no_bus_does_not_crash(tmp_yaml: Path) -> None:
    """bus=None (no bus dep) must not raise — if-guard inside the command holds."""
    deps = CommandDeps(event_bus=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (
        await CommandRegistry.instance().dispatch(
            "config", "set autonomy_level low", make_state()
        )
    ).text
    assert "✓" in result


async def test_config_reset_emits_real_settings(tmp_yaml: Path) -> None:
    """After /config reset, settings_reloaded must carry a real Settings object."""
    from stackowl.config.settings import Settings

    # First set a value to reset
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    await CommandRegistry.instance().dispatch(
        "config", "set autonomy_level high", make_state()
    )

    # Now reset with a spy bus
    CommandRegistry.reset()
    bus = _SpyBus()
    deps = CommandDeps(event_bus=bus)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = (
        await CommandRegistry.instance().dispatch(
            "config", "reset autonomy_level", make_state()
        )
    ).text

    assert "✓" in result
    reload_events = [p for e, p in bus.events if e == "settings_reloaded"]
    assert len(reload_events) == 1
    assert isinstance(reload_events[0], Settings)


# ---------------------------------------------------------------------------
# Honesty — the message must not claim immediate live effect
# ---------------------------------------------------------------------------


async def test_config_set_does_not_claim_applied_immediately(tmp_yaml: Path) -> None:
    """A hot_reload=True field change is honest: does not say 'applied immediately'."""
    deps = CommandDeps(event_bus=MagicMock())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (
        await CommandRegistry.instance().dispatch(
            "config", "set autonomy_level medium", make_state()
        )
    ).text
    assert "✓" in result
    assert "live now" not in result.lower()
    assert "applied immediately" not in result.lower()


# ---------------------------------------------------------------------------
# Smoke — other subcommands reachable via dispatch
# ---------------------------------------------------------------------------


async def test_config_list_works_via_dispatch(tmp_yaml: Path) -> None:
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (await CommandRegistry.instance().dispatch("config", "list", make_state())).text
    assert isinstance(result, str) and result


async def test_config_get_works_via_dispatch(tmp_yaml: Path) -> None:
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = (
        await CommandRegistry.instance().dispatch("config", "get test_mode", make_state())
    ).text
    assert "test_mode" in result
