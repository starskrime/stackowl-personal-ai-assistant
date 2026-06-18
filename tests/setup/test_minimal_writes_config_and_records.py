"""Tests for MinimalSetup — config write, event recording, catalog integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.setup.onboarding_table import OnboardingTable
from stackowl.setup.provider_catalog import ProviderEntry


# Shared fake entries for mocking the catalog
_ANTHROPIC_ENTRY = ProviderEntry(
    name="anthropic",
    label="Anthropic",
    protocol="anthropic",
    base_url="https://api.anthropic.com/v1",
    default_model="claude-sonnet-4-6",
    models=("claude-sonnet-4-6", "claude-haiku-4-5"),
    tier="powerful",
    needs_api_key=True,
    key_url="https://console.anthropic.com/settings/keys",
)
_OPENAI_ENTRY = ProviderEntry(
    name="openai",
    label="OpenAI",
    protocol="openai",
    base_url="https://api.openai.com/v1",
    default_model="gpt-4o",
    models=("gpt-4o", "gpt-4o-mini"),
    tier="powerful",
    needs_api_key=True,
)
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
_CUSTOM_ENTRY = ProviderEntry(
    name="custom",
    label="Custom OpenAI-compatible",
    protocol="openai",
    base_url="",
    default_model="",
    models=(),
    tier="powerful",
    needs_api_key=True,
)

_FAKE_CATALOG = [_ANTHROPIC_ENTRY, _OPENAI_ENTRY, _OLLAMA_ENTRY, _CUSTOM_ENTRY]

# Provider-choice prompt returns "1" (anthropic), model already stubbed via _choose_model.
# Telegram prompt receives "" (skip).
_PROVIDER_THEN_SKIP_TELEGRAM = iter  # create a fresh iter each test


def _seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_LOG_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_PID_FILE", raising=False)
    monkeypatch.delenv("STACKOWL_CONFIG_FILE", raising=False)
    from stackowl.paths import StackowlHome
    StackowlHome.ensure_exists()
    MigrationRunner(db_path=StackowlHome.db_path()).run()


@pytest.mark.asyncio
async def test_minimal_setup_writes_providers_to_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    from stackowl.setup.minimal import MinimalSetup

    # "1" → pick anthropic; "" → accept default base URL; "" → skip Telegram
    inputs = iter(["1", "", ""])

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=_FAKE_CATALOG),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="claude-sonnet-4-6"),
        patch("stackowl.setup.minimal.getpass.getpass", return_value="sk-test-key"),
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
        patch("stackowl.setup.minimal._test_provider_connection", return_value=True),
        patch("stackowl.setup.minimal._store_secret", return_value=("OS keyring", "keychain:anthropic")),
        patch("stackowl.setup.minimal.localize", return_value=""),
    ):
        await MinimalSetup().run()

    config = StackowlHome.config_file()
    assert config.exists(), "stackowl.yaml should be written to ~/.stackowl/"
    content = config.read_text(encoding="utf-8")
    assert "anthropic" in content
    assert "keychain:anthropic" in content


@pytest.mark.asyncio
async def test_minimal_setup_records_completion_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    from stackowl.setup.minimal import MinimalSetup

    inputs = iter(["1", "", ""])  # anthropic; accept default base URL; skip Telegram

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=_FAKE_CATALOG),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="claude-sonnet-4-6"),
        patch("stackowl.setup.minimal.getpass.getpass", return_value="sk-test-key"),
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
        patch("stackowl.setup.minimal._test_provider_connection", return_value=True),
        patch("stackowl.setup.minimal._store_secret", return_value=("OS keyring", "keychain:anthropic")),
        patch("stackowl.setup.minimal.localize", return_value=""),
    ):
        await MinimalSetup().run()

    pool = DbPool(StackowlHome.db_path())
    await pool.open()
    try:
        found = await OnboardingTable.has_event(pool, "minimal_setup_complete")
    finally:
        await pool.close()

    assert found, "minimal_setup_complete event should be recorded"


@pytest.mark.asyncio
async def test_minimal_setup_merges_into_existing_config_without_clobbering_other_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    from stackowl.setup.minimal import MinimalSetup

    StackowlHome.config_file().write_text(
        "log_level: debug\nproviders: []\n", encoding="utf-8"
    )

    inputs = iter(["2", "", ""])  # openai; accept default base URL; skip Telegram

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=_FAKE_CATALOG),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="gpt-4o"),
        patch("stackowl.setup.minimal.getpass.getpass", return_value="sk-openai"),
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
        patch("stackowl.setup.minimal._test_provider_connection", return_value=True),
        patch("stackowl.setup.minimal._store_secret", return_value=("OS keyring", "keychain:openai")),
        patch("stackowl.setup.minimal.localize", return_value=""),
    ):
        await MinimalSetup().run()

    content = StackowlHome.config_file().read_text(encoding="utf-8")
    assert "log_level" in content, "Existing keys must be preserved"
    assert "openai" in content


@pytest.mark.asyncio
async def test_minimal_setup_event_recording_failure_does_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_home(tmp_path, monkeypatch)
    from stackowl.setup.minimal import MinimalSetup

    inputs = iter(["1", "", ""])  # anthropic; accept default base URL; skip Telegram

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=_FAKE_CATALOG),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="claude-sonnet-4-6"),
        patch("stackowl.setup.minimal.getpass.getpass", return_value="sk-test-key"),
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
        patch("stackowl.setup.minimal._test_provider_connection", return_value=True),
        patch("stackowl.setup.minimal._store_secret", return_value=("OS keyring", "keychain:anthropic")),
        patch("stackowl.setup.minimal.localize", return_value=""),
        patch("stackowl.setup.onboarding_table.OnboardingTable.record_event", side_effect=RuntimeError("db down")),
    ):
        # Should not raise even though event recording fails
        await MinimalSetup().run()


@pytest.mark.asyncio
async def test_minimal_setup_with_local_provider_skips_api_key_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_home(tmp_path, monkeypatch)
    from stackowl.setup.minimal import MinimalSetup

    # "3" → picks Ollama; "" → accept default base URL; "" → skip Telegram
    inputs = iter(["3", "", ""])

    mock_getpass = MagicMock()
    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=_FAKE_CATALOG),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="llama3.2"),
        patch("stackowl.setup.minimal.getpass.getpass", mock_getpass),
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
        patch("stackowl.setup.minimal._test_provider_connection", return_value=True),
        patch("stackowl.setup.minimal._store_secret", return_value=("OS keyring", "keychain:ollama")),
        patch("stackowl.setup.minimal.localize", return_value=""),
    ):
        await MinimalSetup().run()

    mock_getpass.assert_not_called()


@pytest.mark.asyncio
async def test_minimal_setup_with_custom_provider_prompts_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    from stackowl.setup.minimal import MinimalSetup

    # "4" → picks custom; then base_url prompt; "" → skip Telegram
    inputs = iter(["4", "https://my.api.example.com/v1", ""])

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=_FAKE_CATALOG),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="my-model-v1"),
        patch("stackowl.setup.minimal.getpass.getpass", return_value="sk-custom-key"),
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
        patch("stackowl.setup.minimal._test_provider_connection", return_value=True),
        patch("stackowl.setup.minimal._store_secret", return_value=("OS keyring", "keychain:custom")),
        patch("stackowl.setup.minimal.localize", return_value=""),
    ):
        await MinimalSetup().run()

    content = StackowlHome.config_file().read_text(encoding="utf-8")
    assert "my.api.example.com" in content
    assert "my-model-v1" in content


@pytest.mark.asyncio
async def test_minimal_setup_writes_telegram_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    from stackowl.setup.minimal import MinimalSetup

    # anthropic; accept default base URL; then Telegram bot token; then user ID
    inputs = iter(["1", "", "bot:faketoken123", "987654321"])

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=_FAKE_CATALOG),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="claude-sonnet-4-6"),
        patch("stackowl.setup.minimal.getpass.getpass", return_value="sk-test-key"),
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
        patch("stackowl.setup.minimal._test_provider_connection", return_value=True),
        patch(
            "stackowl.setup.minimal._store_secret",
            side_effect=[
                ("OS keyring", "keychain:anthropic"),
                ("OS keyring", "keychain:telegram-bot"),
            ],
        ),
        patch("stackowl.setup.minimal.localize", return_value=""),
    ):
        await MinimalSetup().run()

    content = StackowlHome.config_file().read_text(encoding="utf-8")
    assert "telegram_channel" in content
    assert "keychain:telegram-bot" in content
    assert "987654321" in content


@pytest.mark.asyncio
async def test_minimal_setup_overrides_base_url_for_any_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entering a different URL at the base URL prompt writes that URL to config."""
    _seed_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome
    from stackowl.setup.minimal import MinimalSetup

    # anthropic; override base URL; skip Telegram
    inputs = iter(["1", "https://proxy.corp.example/v1", ""])

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=_FAKE_CATALOG),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="claude-sonnet-4-6"),
        patch("stackowl.setup.minimal.getpass.getpass", return_value="sk-test-key"),
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
        patch("stackowl.setup.minimal._test_provider_connection", return_value=True),
        patch("stackowl.setup.minimal._store_secret", return_value=("OS keyring", "keychain:anthropic")),
        patch("stackowl.setup.minimal.localize", return_value=""),
    ):
        await MinimalSetup().run()

    content = StackowlHome.config_file().read_text(encoding="utf-8")
    assert "proxy.corp.example" in content, "Overridden base URL must appear in config"
    assert "api.anthropic.com" not in content, "Bundled URL must be replaced"


@pytest.mark.asyncio
async def test_minimal_setup_connection_test_receives_overridden_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connection test receives an entry with the user-overridden base URL."""
    _seed_home(tmp_path, monkeypatch)
    from stackowl.setup.minimal import MinimalSetup

    captured: list[object] = []

    def _capture_entry(entry: object, api_key: str) -> bool:  # noqa: ARG001
        captured.append(entry)
        return True

    inputs = iter(["1", "https://proxy.corp.example/v1", ""])

    with (
        patch("stackowl.setup.provider_catalog.ProviderCatalog.load", return_value=_FAKE_CATALOG),
        patch("stackowl.setup.minimal.MinimalSetup._choose_model", return_value="claude-sonnet-4-6"),
        patch("stackowl.setup.minimal.getpass.getpass", return_value="sk-test-key"),
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
        patch("stackowl.setup.minimal._test_provider_connection", side_effect=_capture_entry),
        patch("stackowl.setup.minimal._store_secret", return_value=("OS keyring", "keychain:anthropic")),
        patch("stackowl.setup.minimal.localize", return_value=""),
    ):
        await MinimalSetup().run()

    assert len(captured) == 1
    entry = captured[0]
    assert hasattr(entry, "base_url")
    assert entry.base_url == "https://proxy.corp.example/v1"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_choose_model_returns_selected_from_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_choose_model presents numbered list and returns the user's pick."""
    _seed_home(tmp_path, monkeypatch)
    from stackowl.setup.minimal import MinimalSetup

    # "2" → pick the second model in _ANTHROPIC_ENTRY.models (claude-haiku-4-5)
    with (
        patch("stackowl.setup.minimal.typer.prompt", return_value="2"),
        patch("stackowl.setup.minimal.typer.echo"),
    ):
        result = MinimalSetup._choose_model(_ANTHROPIC_ENTRY)

    assert result == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_choose_model_allows_custom_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Picking the N+1 option prompts for a free-text model name."""
    _seed_home(tmp_path, monkeypatch)
    from stackowl.setup.minimal import MinimalSetup

    # 3 = len(models)+1 for _ANTHROPIC_ENTRY which has 2 models → triggers free-text
    inputs = iter(["3", "my-custom-model"])
    with (
        patch("stackowl.setup.minimal.typer.prompt", side_effect=lambda *a, **kw: next(inputs)),
        patch("stackowl.setup.minimal.typer.echo"),
    ):
        result = MinimalSetup._choose_model(_ANTHROPIC_ENTRY)

    assert result == "my-custom-model"


@pytest.mark.asyncio
async def test_choose_model_no_list_prompts_free_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Providers with empty models list go straight to free-text."""
    _seed_home(tmp_path, monkeypatch)
    from stackowl.setup.minimal import MinimalSetup

    with (
        patch("stackowl.setup.minimal.typer.prompt", return_value="my-local-model"),
        patch("stackowl.setup.minimal.typer.echo"),
    ):
        result = MinimalSetup._choose_model(_CUSTOM_ENTRY)

    assert result == "my-local-model"
