"""Tests for the stackowl providers sub-commands: add, remove, edit, test."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from stackowl.cli.app import app
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.setup.provider_catalog import ProviderEntry

runner = CliRunner()


# ── shared fixtures ────────────────────────────────────────────────────────────

def _seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("STACKOWL_HOME", str(home))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_LOG_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_PID_FILE", raising=False)
    monkeypatch.delenv("STACKOWL_CONFIG_FILE", raising=False)
    from stackowl.paths import StackowlHome
    StackowlHome.ensure_exists()
    MigrationRunner(db_path=StackowlHome.db_path()).run()
    return home


def _write_yaml(path: Path, data: dict) -> None:
    y = YAML()
    y.preserve_quotes = True
    with path.open("w", encoding="utf-8") as fh:
        y.dump(data, fh)


def _read_yaml(path: Path) -> dict:
    y = YAML()
    with path.open("r", encoding="utf-8") as fh:
        return y.load(fh) or {}


def _seed_two_providers(config_path: Path) -> None:
    _write_yaml(config_path, {
        "providers": [
            {"name": "ollama", "protocol": "openai", "tier": "fast",
             "base_url": "http://192.168.1.100:11434/v1", "default_model": "llama3.2", "enabled": True},
            {"name": "anthropic", "protocol": "anthropic", "tier": "powerful",
             "base_url": "https://api.anthropic.com/v1", "default_model": "claude-sonnet-4-6",
             "api_key": "keychain:anthropic", "enabled": True},
        ],
        "telegram_channel": {"bot_token": "file:/home/boss/.stackowl/.secrets/tg.key"},
    })


_OLLAMA_ENTRY = ProviderEntry(
    name="ollama",
    label="Ollama (local)",
    protocol="openai",
    base_url="http://localhost:11434/v1",
    default_model="llama3.2",
    models=("llama3.2", "phi4"),
    tier="fast",
    needs_api_key=False,
    is_local=True,
)


# ── providers add ─────────────────────────────────────────────────────────────

def test_providers_add_writes_provider_to_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome

    prompt_responses = iter([
        "http://192.168.1.100:11434/v1",  # Base URL
        "1",                               # _choose_tier → fast (index 1)
    ])

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=[_OLLAMA_ENTRY]),
        patch("stackowl.setup.minimal.MinimalSetup._choose_provider", return_value=_OLLAMA_ENTRY),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="llama3.2"),
        patch("stackowl.setup.minimal._test_provider_connection", return_value=True),
        patch("typer.prompt", side_effect=prompt_responses),
    ):
        result = runner.invoke(app, ["providers", "add"])

    assert result.exit_code == 0, result.output
    data = _read_yaml(StackowlHome.config_file())
    names = [e["name"] for e in data.get("providers", [])]
    assert "ollama" in names


def test_providers_add_aborts_on_existing_provider_when_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    _seed_two_providers(StackowlHome.config_file())

    prompt_responses = iter([
        "http://192.168.1.100:11434/v1",
        "1",
    ])

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=[_OLLAMA_ENTRY]),
        patch("stackowl.setup.minimal.MinimalSetup._choose_provider", return_value=_OLLAMA_ENTRY),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="llama3.2"),
        patch("typer.prompt", side_effect=prompt_responses),
        patch("typer.confirm", return_value=False),  # decline overwrite
    ):
        result = runner.invoke(app, ["providers", "add"])

    assert result.exit_code == 0
    assert "Aborted" in result.output


# ── providers remove ──────────────────────────────────────────────────────────

def test_providers_remove_strips_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    _seed_two_providers(StackowlHome.config_file())

    with patch("typer.confirm", return_value=True):
        result = runner.invoke(app, ["providers", "remove", "ollama"])

    assert result.exit_code == 0, result.output
    data = _read_yaml(StackowlHome.config_file())
    names = [e["name"] for e in data["providers"]]
    assert "ollama" not in names
    assert "anthropic" in names


def test_providers_remove_preserves_telegram_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    _seed_two_providers(StackowlHome.config_file())

    with patch("typer.confirm", return_value=True):
        runner.invoke(app, ["providers", "remove", "ollama"])

    data = _read_yaml(StackowlHome.config_file())
    assert "telegram_channel" in data


def test_providers_remove_deletes_secret_file_when_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome

    secret_file = tmp_path / "my.key"
    secret_file.write_text("sk-test", encoding="utf-8")

    _write_yaml(StackowlHome.config_file(), {
        "providers": [
            {"name": "openai", "protocol": "openai", "tier": "powerful",
             "base_url": "https://api.openai.com/v1", "default_model": "gpt-4o",
             "api_key": f"file:{secret_file}", "enabled": True},
        ],
    })

    with patch("typer.confirm", return_value=True):
        result = runner.invoke(app, ["providers", "remove", "openai"])

    assert result.exit_code == 0, result.output
    assert not secret_file.exists()


def test_providers_remove_keeps_secret_when_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome

    secret_file = tmp_path / "my.key"
    secret_file.write_text("sk-test", encoding="utf-8")

    _write_yaml(StackowlHome.config_file(), {
        "providers": [
            {"name": "openai", "protocol": "openai", "tier": "powerful",
             "base_url": "https://api.openai.com/v1", "default_model": "gpt-4o",
             "api_key": f"file:{secret_file}", "enabled": True},
        ],
    })

    with patch("typer.confirm", side_effect=[True, False]):  # remove=yes, delete secret=no
        result = runner.invoke(app, ["providers", "remove", "openai"])

    assert result.exit_code == 0
    assert secret_file.exists()


def test_providers_remove_missing_returns_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["providers", "remove", "nonexistent"])
    assert result.exit_code == 1


# ── providers edit ────────────────────────────────────────────────────────────

def test_providers_edit_changes_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    _seed_two_providers(StackowlHome.config_file())

    # _choose_tier prompt returns "2" (index 2 = standard),
    # then base_url, model, rate_limit keep current values
    prompt_responses = iter([
        "2",                                         # tier → standard
        "http://192.168.1.100:11434/v1",            # base_url (keep)
        "llama3.2",                                  # model (keep)
        "0",                                         # rate limit (keep)
    ])

    with patch("typer.prompt", side_effect=prompt_responses):
        result = runner.invoke(app, ["providers", "edit", "ollama"])

    assert result.exit_code == 0, result.output
    data = _read_yaml(StackowlHome.config_file())
    ollama = next(e for e in data["providers"] if e["name"] == "ollama")
    assert ollama["tier"] == "standard"
    assert ollama["base_url"] == "http://192.168.1.100:11434/v1"  # unchanged


def test_providers_edit_missing_returns_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["providers", "edit", "nonexistent"])
    assert result.exit_code == 1


def test_providers_edit_no_changes_prints_no_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    _seed_two_providers(StackowlHome.config_file())

    # Return current values unchanged for all prompts
    prompt_responses = iter([
        "1",                                         # tier → fast (same)
        "http://192.168.1.100:11434/v1",            # base_url (same)
        "llama3.2",                                  # model (same)
        "0",                                         # rate limit (same)
    ])

    with patch("typer.prompt", side_effect=prompt_responses):
        result = runner.invoke(app, ["providers", "edit", "ollama"])

    assert result.exit_code == 0
    assert "No changes" in result.output


# ── providers test ────────────────────────────────────────────────────────────

def test_providers_test_reports_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    _seed_two_providers(StackowlHome.config_file())

    with patch("stackowl.setup.minimal._test_provider_connection", return_value=True):
        result = runner.invoke(app, ["providers", "test", "ollama"])

    assert result.exit_code == 0
    assert "reachable" in result.output


def test_providers_test_reports_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    _seed_two_providers(StackowlHome.config_file())

    with patch("stackowl.setup.minimal._test_provider_connection", return_value=False):
        result = runner.invoke(app, ["providers", "test", "ollama"])

    assert result.exit_code == 1
    assert "unreachable" in result.output


def test_providers_test_missing_returns_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["providers", "test", "nonexistent"])
    assert result.exit_code == 1
