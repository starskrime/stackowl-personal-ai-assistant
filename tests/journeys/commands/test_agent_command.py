"""Dispatch test — /agent is wired through CommandRegistry."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.agent_create_command import _NO_PROVIDER, _NO_SCHEDULER
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from stackowl.config.settings import Settings
from stackowl.providers.registry import ProviderRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

_USAGE_MARKER = "Usage: /agent"
_NO_PENDING = "No pending agent proposal"


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_agent_no_subcommand_returns_usage() -> None:
    """Empty args returns the usage block (no crash)."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agent", "", make_state())
    assert _USAGE_MARKER in result


async def test_agent_cancel_no_pending() -> None:
    """/agent cancel with nothing pending returns the no-pending message."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agent", "cancel", make_state())
    assert _NO_PENDING in result


async def test_agent_confirm_no_scheduler() -> None:
    """/agent confirm without scheduler returns the scheduler not-configured message (no crash)."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("agent", "confirm", make_state())
    # Scheduler guard fires before pending-proposal check — no scheduler → exact message
    assert _NO_SCHEDULER in result


async def test_agent_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("agent", "", make_state())


async def test_agent_create_with_provider_registry_populated_skips_not_configured_guard() -> None:
    """C1 — with provider_registry wired, /agent create does NOT return the not-configured message.

    This proves the orchestrator's CommandDeps now passes provider_registry correctly:
    the _NO_PROVIDER guard is bypassed and we only fail at the LLM call (which is mocked).
    """
    real_registry = ProviderRegistry.from_settings(Settings(providers=[]))
    deps = CommandDeps(provider_registry=real_registry)
    register_all_commands(deps, registry=CommandRegistry.instance())

    # Patch the provider lookup so no real network call is made; we just want to
    # confirm the _NO_PROVIDER guard is NOT hit.
    with patch.object(real_registry, "get_by_tier", side_effect=RuntimeError("no providers configured")):
        result = await CommandRegistry.instance().dispatch("agent", "create some-intent", make_state())

    # The provider guard must NOT appear — we got past it (even though the call fails).
    assert _NO_PROVIDER not in result


async def test_agent_create_missing_template_returns_honest_error() -> None:
    """I1 — if the Jinja2 template is missing, the user gets a '✗ /agent create:' message."""
    from jinja2 import TemplateNotFound

    real_registry = ProviderRegistry.from_settings(Settings(providers=[]))
    deps = CommandDeps(provider_registry=real_registry)
    register_all_commands(deps, registry=CommandRegistry.instance())

    with patch(
        "stackowl.commands.agent_create_command.Environment.get_template",
        side_effect=TemplateNotFound("agent_intent.j2"),
    ):
        result = await CommandRegistry.instance().dispatch("agent", "create some-intent", make_state())

    assert result.startswith("✗ /agent create:")
