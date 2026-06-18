"""OPS-1 (F146) — `start` and `serve` share ONE migration site (orchestrator).

Migrations were run twice on every `stackowl start`: once in CLI phase 1 and again
in the orchestrator's phase-1. `serve` ran them once. This asserts a single
migration invocation per boot so the two entrypoints share one boot ordering.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from stackowl.cli.app import app

runner = CliRunner()


def _setup_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "stackowl-home"
    monkeypatch.setenv("STACKOWL_HOME", str(home))
    for var in (
        "STACKOWL_DATA_DIR",
        "STACKOWL_LOG_DIR",
        "STACKOWL_PID_FILE",
        "STACKOWL_CONFIG_FILE",
    ):
        monkeypatch.delenv(var, raising=False)
    return home


def test_start_runs_migrations_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`stackowl start` must invoke MigrationRunner.run exactly once per boot."""
    _setup_home(tmp_path, monkeypatch)

    run_calls = 0
    orig_run = None

    def _counting_run(self: object) -> object:
        nonlocal run_calls
        run_calls += 1
        assert orig_run is not None
        return orig_run(self)

    from stackowl.db.migrations.runner import MigrationRunner

    orig_run = MigrationRunner.run

    with (
        patch("stackowl.setup.minimal.MinimalSetup") as mock_setup_cls,
        patch("stackowl.startup.orchestrator.StartupOrchestrator") as mock_orch_cls,
        patch("stackowl.config.settings.Settings") as mock_settings_cls,
        patch("stackowl.infra.observability.setup_logging"),
        patch.object(MigrationRunner, "run", _counting_run),
    ):
        mock_setup_cls.return_value.run = AsyncMock()
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock()
        # The orchestrator owns the single migration site; the CLI delegates the
        # pre-onboarding schema guarantee to the SAME orchestrator instance.
        mock_orch.ensure_migrations = MagicMock(
            side_effect=lambda: MigrationRunner(
                db_path=__import__(
                    "stackowl.paths", fromlist=["StackowlHome"]
                ).StackowlHome.db_path()
            ).run()
        )
        mock_orch_cls.return_value = mock_orch
        mock_settings_cls.return_value.providers = []

        result = runner.invoke(app, ["start", "--skip-setup"])

    assert result.exit_code == 0, result.output
    assert run_calls == 1, f"migrations ran {run_calls} times, expected exactly 1"
