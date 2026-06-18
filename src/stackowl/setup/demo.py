"""DemoSetup — non-interactive demo mode onboarding."""

from __future__ import annotations

import asyncio
import time

import typer

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.setup.localize import localize


class DemoSetup:
    """Configures StackOwl in MockProvider demo mode — no API key required."""

    def run(self) -> None:
        """Print demo intro and MockProvider setup instructions."""
        # 1. ENTRY
        log.setup.info("[demo] DemoSetup.run: entry")
        t0 = time.monotonic()

        # 2. DECISION — always non-interactive, just print and return
        typer.echo(localize("setup_demo_intro"))

        # 3. STEP — write a minimal demo config if none exists
        self._ensure_demo_config()

        # 3b. STEP — record completion event
        self._record_completion()

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        log.setup.info(
            "[demo] DemoSetup.run: exit",
            extra={"_fields": {"duration_ms": duration_ms}},
        )

    def _ensure_demo_config(self) -> None:
        """Write a minimal demo stackowl.yaml under ~/.stackowl/ if not present."""
        # 1. ENTRY
        log.setup.debug("[demo] _ensure_demo_config: entry")

        config_path = StackowlHome.config_file()
        if config_path.exists():
            log.setup.debug(
                "[demo] _ensure_demo_config: config already exists — skipping",
                extra={"_fields": {"path": str(config_path)}},
            )
            return

        # 2. DECISION — write minimal demo config
        demo_content = (
            "# StackOwl Demo Configuration\n"
            "test_mode: false\n"
            "providers:\n"
            "  - name: mock\n"
            "    protocol: openai\n"
            "    base_url: http://localhost:9999\n"
            "    api_key: null\n"
            "    default_model: mock-model\n"
            "    tier: fast\n"
            "    enabled: true\n"
        )
        # 3. STEP — write file
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(demo_content, encoding="utf-8")
            typer.echo(f"  ✓ Demo config written to {config_path}")
            log.setup.info(
                "[demo] _ensure_demo_config: exit — config written",
                extra={"_fields": {"path": str(config_path)}},
            )
        except OSError as exc:
            typer.echo(f"  ⚠ Could not write demo config: {exc}")
            log.setup.warning(
                "[demo] _ensure_demo_config: write failed",
                extra={"_fields": {"reason": str(exc)}},
            )

    def _record_completion(self) -> None:
        """Record demo_setup_complete so stackowl start won't re-prompt."""
        try:
            from stackowl.db.migrations.runner import MigrationRunner
            from stackowl.db.pool import DbPool
            from stackowl.setup.onboarding_table import OnboardingTable

            MigrationRunner(db_path=StackowlHome.db_path()).run()

            async def _record() -> None:
                pool = DbPool(StackowlHome.db_path())
                await pool.open()
                try:
                    await OnboardingTable.record_event(pool, "demo_setup_complete")
                    await OnboardingTable.record_event(pool, "minimal_setup_complete")
                finally:
                    await pool.close()

            asyncio.run(_record())
            log.setup.debug("[demo] _record_completion: event recorded")
        except Exception as exc:
            log.setup.warning(
                "[demo] _record_completion: could not record event — %s", exc
            )
