"""Dispatch test — /whoami is wired through CommandRegistry."""
from __future__ import annotations
import pytest
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


class _FakeOwlRegistry:
    """Minimal fake that returns a manifest with role/model_tier/provider_name."""
    def get(self, name: str) -> object:
        class M:
            role = "primary-assistant"
            model_tier = "powerful"
            provider_name = "anthropic"
        return M()


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_whoami_includes_role_and_tier() -> None:
    deps = CommandDeps(owl_registry=_FakeOwlRegistry())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("whoami", "", make_state())
    assert "primary-assistant" in result
    assert "powerful" in result


async def test_whoami_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("whoami", "", make_state())


async def test_whoami_degrades_without_registry() -> None:
    """With no owl_registry, /whoami still returns basic identity (no crash,
    no silent failure) — covers the honest-degradation path."""
    register_all_commands(CommandDeps(owl_registry=None), registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("whoami", "", make_state())
    assert "Owl:" in result
    assert "Channel:" in result
    assert "Session:" in result
