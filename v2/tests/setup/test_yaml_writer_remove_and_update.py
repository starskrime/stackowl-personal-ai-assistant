"""Tests for yaml_writer.remove_provider_config and yaml_writer.update_provider_field."""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from stackowl.setup.yaml_writer import remove_provider_config, update_provider_field


def _write_yaml(path: Path, data: dict) -> None:
    y = YAML()
    y.preserve_quotes = True
    with path.open("w", encoding="utf-8") as fh:
        y.dump(data, fh)


def _read_yaml(path: Path) -> dict:
    y = YAML()
    with path.open("r", encoding="utf-8") as fh:
        return y.load(fh) or {}


def _seed(path: Path) -> None:
    _write_yaml(path, {
        "providers": [
            {"name": "ollama", "protocol": "openai", "tier": "fast", "base_url": "http://localhost:11434/v1"},
            {"name": "anthropic", "protocol": "anthropic", "tier": "powerful", "base_url": "https://api.anthropic.com/v1"},
        ],
        "telegram_channel": {"bot_token": "file:/home/boss/.stackowl/.secrets/telegram-bot.key"},
    })


def test_remove_provider_pops_named_entry(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _seed(cfg)
    result = remove_provider_config(cfg, "ollama")
    assert result is True
    data = _read_yaml(cfg)
    names = [e["name"] for e in data["providers"]]
    assert "ollama" not in names
    assert "anthropic" in names


def test_remove_provider_missing_returns_false(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _seed(cfg)
    result = remove_provider_config(cfg, "lmstudio")
    assert result is False
    data = _read_yaml(cfg)
    assert len(data["providers"]) == 2


def test_remove_provider_preserves_other_top_level_keys(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _seed(cfg)
    remove_provider_config(cfg, "ollama")
    data = _read_yaml(cfg)
    assert "telegram_channel" in data
    assert data["telegram_channel"]["bot_token"] == "file:/home/boss/.stackowl/.secrets/telegram-bot.key"


def test_remove_provider_no_config_file_returns_false(tmp_path: Path) -> None:
    cfg = tmp_path / "missing.yaml"
    assert remove_provider_config(cfg, "ollama") is False


def test_update_provider_field_changes_tier(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _seed(cfg)
    result = update_provider_field(cfg, "anthropic", "tier", "standard")
    assert result is True
    data = _read_yaml(cfg)
    anthropic = next(e for e in data["providers"] if e["name"] == "anthropic")
    assert anthropic["tier"] == "standard"


def test_update_provider_field_missing_provider_returns_false(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _seed(cfg)
    result = update_provider_field(cfg, "nonexistent", "tier", "fast")
    assert result is False


def test_update_provider_field_preserves_sibling_providers(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _seed(cfg)
    update_provider_field(cfg, "ollama", "base_url", "http://192.168.1.100:11434/v1")
    data = _read_yaml(cfg)
    ollama = next(e for e in data["providers"] if e["name"] == "ollama")
    anthropic = next(e for e in data["providers"] if e["name"] == "anthropic")
    assert ollama["base_url"] == "http://192.168.1.100:11434/v1"
    assert anthropic["tier"] == "powerful"


def test_update_provider_field_no_config_file_returns_false(tmp_path: Path) -> None:
    cfg = tmp_path / "missing.yaml"
    assert update_provider_field(cfg, "ollama", "tier", "fast") is False


def test_update_provider_field_adds_new_field(tmp_path: Path) -> None:
    cfg = tmp_path / "stackowl.yaml"
    _seed(cfg)
    update_provider_field(cfg, "ollama", "rate_limit_rpm", 60)
    data = _read_yaml(cfg)
    ollama = next(e for e in data["providers"] if e["name"] == "ollama")
    assert ollama["rate_limit_rpm"] == 60
