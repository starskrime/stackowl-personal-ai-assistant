"""OnboardingCommand journey tests — button-driven first-run wizard."""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import CommandResponse
from tests._story_6_7_helpers import make_settings, make_state


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_onboarding_bare_shows_provider_step() -> None:
    deps = CommandDeps(settings=make_settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch("onboarding", "", make_state())

    assert isinstance(result, CommandResponse)
    assert "provider" in result.text.lower()
    assert len(result.actions) >= 1


async def test_onboarding_provider_already_configured_offers_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stackowl.commands.config_helpers.config_path", lambda: tmp_path / "stackowl.yaml"
    )
    from stackowl.commands.config_helpers import save_yaml

    save_yaml(
        tmp_path / "stackowl.yaml",
        {
            "providers": [
                {
                    "name": "acme",
                    "protocol": "openai",
                    "enabled": True,
                    "default_model": "gpt-4o",
                    "tier": "powerful",
                }
            ]
        },
    )
    deps = CommandDeps(settings=make_settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch("onboarding", "", make_state())

    assert "already" in result.text.lower() or "skip" in result.text.lower()


async def test_onboarding_step_autonomy_shows_three_level_buttons() -> None:
    deps = CommandDeps(settings=make_settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch("onboarding", "step=autonomy", make_state())

    labels = {a.label.lower() for a in result.actions}
    assert {"low", "medium", "high"} <= labels
