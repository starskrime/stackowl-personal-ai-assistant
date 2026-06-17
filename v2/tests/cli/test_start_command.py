"""Tests for `stackowl start` command."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from stackowl.cli.app import app
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.setup.onboarding_table import OnboardingTable

runner = CliRunner()


def _wire_real_migrations(mock_orch: MagicMock) -> None:
    """Make a mocked orchestrator's single migration site (F146) actually migrate.

    `cli start` now delegates its pre-onboarding schema guarantee to the
    orchestrator's `ensure_migrations` (the ONE migration site). When the
    orchestrator is mocked, that delegation must still build the schema so the
    first-run/onboarding detection query has its table.
    """
    from stackowl.paths import StackowlHome

    mock_orch.ensure_migrations = MagicMock(
        side_effect=lambda: MigrationRunner(db_path=StackowlHome.db_path()).run()
    )


def _setup_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "stackowl-home"
    monkeypatch.setenv("STACKOWL_HOME", str(home))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_LOG_DIR", raising=False)
    monkeypatch.delenv("STACKOWL_PID_FILE", raising=False)
    monkeypatch.delenv("STACKOWL_CONFIG_FILE", raising=False)
    return home


def test_start_creates_home_on_fresh_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup_home(tmp_path, monkeypatch)

    # Local imports inside start() — patch at source module
    with (
        patch("stackowl.setup.minimal.MinimalSetup") as mock_setup_cls,
        patch("stackowl.startup.orchestrator.StartupOrchestrator") as mock_orch_cls,
        patch("stackowl.config.settings.Settings") as mock_settings_cls,
        patch("stackowl.infra.observability.setup_logging"),
    ):
        mock_setup_cls.return_value.run = AsyncMock()
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock()
        _wire_real_migrations(mock_orch)
        mock_orch_cls.return_value = mock_orch
        mock_settings_cls.return_value.providers = []

        result = runner.invoke(app, ["start"])

    assert home.exists(), "Home directory should be created by start"
    assert (home / "workspace").exists()
    assert (home / ".secrets").exists()
    assert result.exit_code == 0, result.output


def test_start_first_run_invokes_minimal_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup_home(tmp_path, monkeypatch)

    with (
        patch("stackowl.setup.minimal.MinimalSetup") as mock_setup_cls,
        patch("stackowl.startup.orchestrator.StartupOrchestrator") as mock_orch_cls,
        patch("stackowl.config.settings.Settings") as mock_settings_cls,
        patch("stackowl.infra.observability.setup_logging"),
    ):
        mock_run = AsyncMock()
        mock_setup_cls.return_value.run = mock_run
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock()
        _wire_real_migrations(mock_orch)
        mock_orch_cls.return_value = mock_orch
        mock_settings_cls.return_value.providers = []

        result = runner.invoke(app, ["start"])

    assert result.exit_code == 0, result.output
    mock_run.assert_awaited_once()


def test_start_skips_setup_when_event_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup_home(tmp_path, monkeypatch)
    from stackowl.paths import StackowlHome

    MigrationRunner(db_path=StackowlHome.db_path()).run()

    async def _seed() -> None:
        pool = DbPool(StackowlHome.db_path())
        await pool.open()
        try:
            await OnboardingTable.record_event(pool, "minimal_setup_complete")
        finally:
            await pool.close()

    asyncio.run(_seed())

    with (
        patch("stackowl.setup.minimal.MinimalSetup") as mock_setup_cls,
        patch("stackowl.startup.orchestrator.StartupOrchestrator") as mock_orch_cls,
        patch("stackowl.config.settings.Settings") as mock_settings_cls,
        patch("stackowl.infra.observability.setup_logging"),
    ):
        mock_run = AsyncMock()
        mock_setup_cls.return_value.run = mock_run
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock()
        _wire_real_migrations(mock_orch)
        mock_orch_cls.return_value = mock_orch
        mock_settings_cls.return_value.providers = []

        result = runner.invoke(app, ["start"])

    assert result.exit_code == 0, result.output
    mock_run.assert_not_awaited()


def test_start_skip_setup_flag_overrides_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup_home(tmp_path, monkeypatch)

    with (
        patch("stackowl.setup.minimal.MinimalSetup") as mock_setup_cls,
        patch("stackowl.startup.orchestrator.StartupOrchestrator") as mock_orch_cls,
        patch("stackowl.config.settings.Settings") as mock_settings_cls,
        patch("stackowl.infra.observability.setup_logging"),
    ):
        mock_run = AsyncMock()
        mock_setup_cls.return_value.run = mock_run
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock()
        _wire_real_migrations(mock_orch)
        mock_orch_cls.return_value = mock_orch
        mock_settings_cls.return_value.providers = []

        result = runner.invoke(app, ["start", "--skip-setup"])

    assert result.exit_code == 0, result.output
    mock_run.assert_not_awaited()


def test_start_exits_on_config_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _setup_home(tmp_path, monkeypatch)
    from stackowl.exceptions import ConfigurationError

    provider = MagicMock()
    provider.name = "anthropic"
    provider.api_key = "keychain:missing"

    with (
        patch("stackowl.setup.minimal.MinimalSetup") as mock_setup_cls,
        patch("stackowl.startup.orchestrator.StartupOrchestrator") as mock_orch_cls,
        patch("stackowl.config.settings.Settings") as mock_settings_cls,
        patch("stackowl.config.secret_resolver.SecretResolver.resolve", side_effect=ConfigurationError("not found")),
        patch("stackowl.infra.observability.setup_logging"),
    ):
        mock_setup_cls.return_value.run = AsyncMock()
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock()
        _wire_real_migrations(mock_orch)
        mock_orch_cls.return_value = mock_orch
        mock_settings_cls.return_value.providers = [provider]

        result = runner.invoke(app, ["start", "--skip-setup"])

    assert result.exit_code != 0
    assert "stackowl setup --minimal" in result.output
