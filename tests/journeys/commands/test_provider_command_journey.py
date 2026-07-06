"""Dispatch tests — /provider is wired through CommandRegistry with a live bus.

Epic C1: proves the dead-bus regression is fixed for /provider.

NOTE: a unit-level test suite already lives at tests/commands/test_provider_command.py
(covers list/add/remove/set-tier exhaustively, driving the command directly).
These journey tests focus on REGISTRY WIRING and BUS EMISSION — the two things
the unit tests cannot catch (they construct ProviderCommand with a self-injected
_SpyBus, bypassing the DI path entirely).

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


async def test_provider_reachable_via_assembly(tmp_yaml: Path) -> None:
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("provider", "list", make_state())
    assert isinstance(result, str) and result


async def test_provider_not_found_without_registration() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("provider", "list", make_state())


# ---------------------------------------------------------------------------
# Bus wiring — the production bus must fire (catches the bus=None regression)
# ---------------------------------------------------------------------------


async def test_provider_add_emits_on_production_bus(tmp_yaml: Path) -> None:
    """The event_bus in CommandDeps must receive settings_reloaded after /provider add.

    Regression gate: with Pattern-A the command was self-registered with
    event_bus=None, so _emit_reloaded was always a no-op.
    """
    from stackowl.config.settings import Settings

    spy = MagicMock()

    deps = CommandDeps(event_bus=spy)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "provider", "add acme openai gpt-x fast", make_state()
    )

    assert "✓" in result or "added" in result.lower()
    spy.emit.assert_called_once()
    emitted_event, emitted_payload = spy.emit.call_args[0]
    assert emitted_event == "settings_reloaded"
    assert isinstance(emitted_payload, Settings)
    # NOTE: gates the dead-bus regression (emit now reaches the production bus).
    # The live provider reload is driven by a real Settings object payload,
    # enabling type-guarded subscribers to apply it immediately.


async def test_provider_remove_emits_on_production_bus(tmp_yaml: Path) -> None:
    # Add first (without a bus, just to set up state)
    deps_no_bus = CommandDeps(event_bus=None)
    register_all_commands(deps_no_bus, registry=CommandRegistry.instance())
    await CommandRegistry.instance().dispatch(
        "provider", "add acme openai gpt-x fast", make_state()
    )

    # Now remove with a spy bus wired
    CommandRegistry.reset()
    spy = MagicMock()
    deps = CommandDeps(event_bus=spy)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "provider", "remove acme", make_state()
    )

    assert "✓" in result or "removed" in result.lower()
    spy.emit.assert_called_once()
    assert spy.emit.call_args[0][0] == "settings_reloaded"


async def test_provider_no_bus_does_not_crash(tmp_yaml: Path) -> None:
    deps = CommandDeps(event_bus=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "provider", "add acme openai gpt-x fast", make_state()
    )
    assert "✓" in result or "added" in result.lower()


# ---------------------------------------------------------------------------
# Honesty — /provider messages must say "applied immediately"
# ---------------------------------------------------------------------------


async def test_provider_add_message_is_honest_about_timing(tmp_yaml: Path) -> None:
    """/provider add must say 'applied immediately' since emit now carries a real Settings."""
    deps = CommandDeps(event_bus=MagicMock())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "provider", "add acme openai gpt-x fast", make_state()
    )
    assert "immediately" in result.lower()
