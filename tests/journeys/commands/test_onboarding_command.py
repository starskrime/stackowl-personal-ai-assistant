"""OnboardingCommand journey tests — button-driven first-run wizard."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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


async def test_onboarding_step_autonomy_write_lands_and_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delegated /config set must actually persist, not just look successful."""
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(yaml.dump({"providers": []}), encoding="utf-8")
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    deps = CommandDeps(settings=make_settings())
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "onboarding", "step=autonomy value=high", make_state()
    )

    assert isinstance(result, CommandResponse)
    assert "autonomy set to high" in result.text.lower()
    on_disk = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    assert on_disk.get("autonomy_level") == "high"


async def test_onboarding_step_autonomy_surfaces_delegated_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the delegated /config set fails, onboarding must not claim success."""
    deps = CommandDeps(settings=make_settings())
    register_all_commands(deps, registry=CommandRegistry.instance())
    # STACKOWL_CONFIG_FILE resolves to a directory, not a file — save_yaml's
    # os.replace onto it raises, so the delegated /config set call returns a
    # real "✗ ..." failure (config_path() reads the env var fresh on every call).
    # Set only now — Settings() construction above also reads this env var.
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(tmp_path))

    result = await CommandRegistry.instance().dispatch(
        "onboarding", "step=autonomy value=high", make_state()
    )

    assert isinstance(result, CommandResponse)
    assert "autonomy set to high" not in result.text.lower()
    assert result.text.startswith("✗")
