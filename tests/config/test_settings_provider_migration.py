"""Confirms Settings() triggers the on-disk provider-tier migration exactly
once per load, at the single choke point every Settings() construction
(across the whole codebase) already goes through."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_settings_construction_migrates_a_legacy_yaml_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(
        "test_mode: true\n"
        "providers:\n"
        "  - name: groq\n"
        "    protocol: openai\n"
        "    default_model: m\n"
        "    tier: fast\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))

    from stackowl.config.settings import Settings

    settings = Settings()

    assert settings.providers[0].tiers == ("fast",)
    # The FILE itself was rewritten, not just normalized in memory.
    from ruamel.yaml import YAML
    with cfg.open("r", encoding="utf-8") as fh:
        raw = YAML().load(fh)
    assert raw["providers"][0]["tiers"] == ["fast"]
    assert "tier" not in raw["providers"][0]


def test_settings_construction_is_idempotent_on_an_already_migrated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "stackowl.yaml"
    original = (
        "test_mode: true\n"
        "providers:\n"
        "  - name: groq\n"
        "    protocol: openai\n"
        "    default_model: m\n"
        "    tiers: [fast, standard]\n"
    )
    cfg.write_text(original, encoding="utf-8")
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))

    from stackowl.config.settings import Settings

    Settings()

    assert cfg.read_text(encoding="utf-8") == original
