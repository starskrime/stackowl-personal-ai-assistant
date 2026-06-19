"""Dispatch test — /webhook is wired through CommandRegistry."""
from __future__ import annotations
import pytest
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_settings, make_state, no_test_mode_guard  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_webhook_usage_with_no_args() -> None:
    deps = CommandDeps(db=object(), settings=make_settings())  # type: ignore[arg-type]
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("webhook", "", make_state())
    assert "Usage:" in result


async def test_webhook_register_returns_yaml_stanza() -> None:
    CommandRegistry.reset()
    deps = CommandDeps(db=object(), settings=make_settings())  # type: ignore[arg-type]
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("webhook", "register mygithub", make_state())
    assert "stackowl.yaml" in result or "YAML" in result.lower() or "yaml" in result


async def test_webhook_not_configured_when_db_none() -> None:
    CommandRegistry.reset()
    deps = CommandDeps(db=None, settings=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("webhook", "", make_state())
    assert "not configured" in result


async def test_webhook_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("webhook", "", make_state())
