"""StackOwl root CLI application — subcommand groups and version output."""

import logging
from pathlib import Path

import typer

from stackowl.version import API_VERSION, VERSION

log = logging.getLogger("stackowl.cli")

app = typer.Typer(
    name="stackowl",
    help="StackOwl — Personal AI assistant framework with owl personas.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

serve_app = typer.Typer(help="Start and manage the StackOwl server.")
health_app = typer.Typer(help="Show and monitor system health.")
db_app = typer.Typer(help="Database management commands.")
mcp_app = typer.Typer(help="MCP server operations.")
plugins_app = typer.Typer(help="Plugin management commands.")
integrations_app = typer.Typer(help="External integration management.")

from stackowl.cli.models import models_app  # noqa: E402
from stackowl.cli.providers_cli import providers_app  # noqa: E402

app.add_typer(serve_app, name="serve")
app.add_typer(health_app, name="health")
app.add_typer(db_app, name="db")
app.add_typer(providers_app, name="providers")
app.add_typer(models_app, name="models")
app.add_typer(mcp_app, name="mcp")
app.add_typer(plugins_app, name="plugins")
app.add_typer(integrations_app, name="integrations")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"stackowl {VERSION}")
        typer.echo(f"api_version={API_VERSION}")
        raise typer.Exit()


@app.callback()
def callback(
    _version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Print version and exit.",
        is_eager=True,
        callback=_version_callback,
    ),
) -> None:
    """StackOwl — Personal AI assistant framework with owl personas."""


@app.command()
def validate_config() -> None:
    """Validate stackowl.yaml: load settings and resolve all provider secrets."""
    import sys

    from stackowl.config.secret_resolver import SecretResolver
    from stackowl.config.settings import Settings
    from stackowl.exceptions import ConfigurationError

    try:
        settings = Settings()
    except Exception as exc:
        typer.echo(f"✗ Failed to load settings: {exc}", err=True)
        raise typer.Exit(1) from exc

    if not settings.providers:
        typer.echo("⚠ No providers configured")
        raise typer.Exit(0)

    all_ok = True
    for provider in settings.providers:
        tag = f"{provider.name} [{provider.protocol}]"
        if provider.api_key is None:
            typer.echo(f"✓ {tag}: ok (no api_key required)")
            continue
        try:
            SecretResolver.resolve(provider.api_key)
            typer.echo(f"✓ {tag}: ok")
        except ConfigurationError as exc:
            log.warning("validate_config: provider %s secret unresolvable: %s", tag, exc)
            typer.echo(f"✗ {tag}: {exc}")
            all_ok = False

    if not all_ok:
        sys.exit(1)


@app.command()
def init() -> None:
    """Initialize a new StackOwl installation."""
    typer.echo("init: not yet implemented")


@app.command()
def start(
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate boot without starting the server."),
    skip_setup: bool = typer.Option(False, "--skip-setup", help="Boot without running first-run onboarding."),
) -> None:
    """Boot StackOwl — ensures ~/.stackowl/ exists, runs onboarding if needed, then serves."""
    import asyncio
    import sys

    from stackowl.config.secret_resolver import SecretResolver
    from stackowl.config.settings import Settings
    from stackowl.db.migrations.runner import MigrationRunner
    from stackowl.db.pool import DbPool
    from stackowl.exceptions import ConfigurationError, StartupError
    from stackowl.infra.observability import setup_logging
    from stackowl.paths import StackowlHome
    from stackowl.setup.minimal import MinimalSetup
    from stackowl.setup.onboarding_table import OnboardingTable
    from stackowl.startup.orchestrator import StartupOrchestrator

    setup_logging()

    # Phase 0 — HOME: ensure ~/.stackowl/ tree exists
    log.debug("[cli] start: phase 0 — ensure home tree")
    StackowlHome.ensure_exists()
    typer.echo(f"StackOwl home: {StackowlHome.home()}")

    # Phase 1 — MIGRATE: apply any pending migrations
    log.debug("[cli] start: phase 1 — migrations")
    MigrationRunner(db_path=StackowlHome.db_path()).run()

    # Phase 2 — DETECT FIRST RUN
    log.debug("[cli] start: phase 2 — first-run detection")

    async def _check_first_run() -> bool:
        pool = DbPool(StackowlHome.db_path())
        await pool.open()
        try:
            return not await OnboardingTable.has_event(pool, "minimal_setup_complete")
        finally:
            await pool.close()

    first_run = asyncio.run(_check_first_run())

    # Phase 3 — ONBOARD (only if first run and not --skip-setup)
    if first_run and not skip_setup:
        log.debug("[cli] start: phase 3 — onboarding")
        typer.echo("Welcome to StackOwl. Let's get you set up.")
        asyncio.run(MinimalSetup().run())
    else:
        log.debug(
            "[cli] start: phase 3 — skipped (first_run=%s skip_setup=%s)",
            first_run,
            skip_setup,
        )

    # Phase 4 — VALIDATE CONFIG
    log.debug("[cli] start: phase 4 — validate config")
    try:
        settings = Settings()
    except Exception as exc:
        typer.echo(f"✗ Config invalid: {exc}", err=True)
        typer.echo("  Run `stackowl setup --minimal` to configure.", err=True)
        raise typer.Exit(1) from exc

    if settings.providers:
        all_ok = True
        for provider in settings.providers:
            if provider.api_key is None:
                continue
            try:
                SecretResolver.resolve(provider.api_key)
            except ConfigurationError as exc:
                typer.echo(f"✗ Config invalid: {provider.name} — {exc}", err=True)
                typer.echo("  Run `stackowl setup --minimal` to (re)configure.", err=True)
                all_ok = False
        if not all_ok:
            raise typer.Exit(1)

    # Phase 5 — SERVE
    log.debug("[cli] start: phase 5 — serve")
    try:
        asyncio.run(StartupOrchestrator(dry_run=dry_run).run())
    except StartupError as exc:
        typer.echo(f"✗ Startup failed: {exc}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        log.debug("[cli] start: interrupted")
        sys.exit(0)


@serve_app.callback(invoke_without_command=True)
def serve(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate startup without modifying state."),
) -> None:
    """Start the StackOwl server."""
    if ctx.invoked_subcommand is not None:
        return
    import asyncio
    import sys

    from stackowl.exceptions import StartupError
    from stackowl.infra.observability import setup_logging
    from stackowl.startup.orchestrator import StartupOrchestrator

    setup_logging()
    try:
        asyncio.run(StartupOrchestrator(dry_run=dry_run).run())
    except StartupError as exc:
        log.warning("serve: startup failed: %s", exc)
        typer.echo(str(exc), err=True)
        sys.exit(1)

    if dry_run:
        typer.echo("✓ Dry run complete — no state was modified")


@health_app.callback(invoke_without_command=True)
def health(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit health status as JSON."),
) -> None:
    """Show system health status for all subsystems."""
    if ctx.invoked_subcommand is not None:
        return
    import asyncio
    import json
    import sys

    from stackowl.config.settings import Settings
    from stackowl.db.pool import default_db_path
    from stackowl.health.aggregator import HealthAggregator
    from stackowl.health.contributors import (
        BrowserContributor,
        DbContributor,
        FilesystemContributor,
        ProviderContributor,
    )
    from stackowl.startup.fs_probe import _data_dir, _log_dir

    settings = Settings()
    agg = HealthAggregator()
    agg.register(DbContributor(default_db_path()))
    agg.register(FilesystemContributor(_data_dir(), _log_dir()))
    # Browser contributor — no live runtime in CLI context (different process),
    # so it always reports 'degraded — runtime not constructed' from here.
    # /browser settings inside the serve process gives live status.
    agg.register(BrowserContributor(runtime=None, sessions=None))
    for provider in settings.providers:
        if provider.enabled:
            agg.register(ProviderContributor(provider))
    # ResilienceContributor needs live HealableResource refs from inside
    # `stackowl serve` (browser runtime, db pool, providers, etc.) — the
    # out-of-process CLI doesn't have those. It's available for use by a
    # future in-process /health slash command. Wiring here would just
    # report "no resources registered". See plan Commit E.

    statuses = asyncio.run(agg.collect())

    if as_json:
        payload = [
            {
                "name": s.name,
                "status": s.status,
                "message": s.message,
                "latency_ms": round(s.latency_ms, 1),
            }
            for s in statuses
        ]
        typer.echo(json.dumps(payload, indent=2))
    else:
        for s in statuses:
            icon = "✓" if s.status == "ok" else ("⚠" if s.status == "degraded" else "✗")
            msg = f"  {s.message}" if s.message else ""
            typer.echo(f"{icon}  {s.name:<30} {s.status:<10} {s.latency_ms:>6.0f}ms{msg}")

    if any(s.status != "ok" for s in statuses):
        sys.exit(1)


@db_app.callback()
def db() -> None:
    """Database management commands."""


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply all pending schema migrations."""
    import sys

    from stackowl.db.migrations.runner import MigrationResult, MigrationRunner
    from stackowl.db.pool import default_db_path
    from stackowl.exceptions import MigrationError

    runner = MigrationRunner(db_path=default_db_path())
    try:
        results: list[MigrationResult] = runner.run()
    except MigrationError as exc:
        log.warning("db_migrate: migration failed: %s", exc)
        typer.echo(f"✗ Migration {exc.migration} failed: {exc.reason}", err=True)
        sys.exit(1)

    for r in results:
        if r.action == "applied":
            typer.echo(f"✓ migration {r.version} applied")
        else:
            typer.echo(f"— {r.version} already applied")

    versions = [r.version for r in results if r.action == "applied"] or [r.version for r in results]
    final_version = max(versions) if versions else "0000"
    typer.echo(f"✓ Database schema up to date (version: {final_version})")


@db_app.command("backup")
def db_backup(
    output: Path = typer.Argument(..., help="Destination path for the backup file."),
) -> None:
    """Back up the database using VACUUM INTO (hot backup, no downtime)."""
    import sqlite3
    import sys

    from stackowl.config.test_mode import TestModeGuard, TestModeViolation
    from stackowl.db.pool import default_db_path

    try:
        TestModeGuard.assert_not_test_mode("db.backup")
    except TestModeViolation as exc:
        log.warning("db_backup: blocked in test mode: %s", exc)
        typer.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    db_path = default_db_path()
    log.debug("[db] db_backup: entry — source=%s output=%s", db_path, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(f"VACUUM INTO '{output}'")
        conn.close()
    except Exception as exc:
        log.warning("[db] db_backup: failed: %s", exc)
        typer.echo(f"✗ Backup failed: {exc}", err=True)
        sys.exit(1)

    size = output.stat().st_size
    log.info("[db] db_backup: exit — output=%s size=%d", output, size)
    typer.echo(f"✓ Backup written to {output} ({size:,} bytes)")


@db_app.command("restore")
def db_restore(
    input: Path = typer.Argument(..., help="Path to the backup file to restore from."),
) -> None:
    """Restore the database from a backup file (prompts for confirmation)."""
    import sqlite3
    import sys

    from stackowl.config.test_mode import TestModeGuard, TestModeViolation
    from stackowl.db.pool import default_db_path

    try:
        TestModeGuard.assert_not_test_mode("db.restore")
    except TestModeViolation as exc:
        log.warning("db_restore: blocked in test mode: %s", exc)
        typer.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    log.debug("[db] db_restore: entry — input=%s", input)
    try:
        check_conn = sqlite3.connect(input)
        result = check_conn.execute("PRAGMA integrity_check").fetchone()
        check_conn.close()
        if result is None or result[0] != "ok":
            raise ValueError(f"integrity_check returned: {result}")
    except Exception as exc:
        log.warning("[db] db_restore: integrity check failed: %s", exc)
        typer.echo("✗ Restore file is not a valid SQLite database", err=True)
        sys.exit(1)

    confirm = typer.prompt("Restore will replace the current database. Type YES to confirm")
    if confirm != "YES":
        typer.echo("Restore cancelled")
        return

    db_path = default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        input.replace(db_path)
    except Exception as exc:
        log.warning("[db] db_restore: replace failed: %s", exc)
        typer.echo(f"✗ Restore failed: {exc}", err=True)
        sys.exit(1)

    log.info("[db] db_restore: exit — restored %s → %s", input, db_path)
    typer.echo(f"✓ Database restored from {input}")


# ---------------------------------------------------------------------------
# MCP server commands
# ---------------------------------------------------------------------------


@mcp_app.callback()
def mcp() -> None:
    """MCP server operations."""


@mcp_app.command("start")
def mcp_start(
    transport: str = typer.Option("sse", help="Transport: sse or stdio"),
) -> None:
    """Start the MCP server."""
    import asyncio

    from stackowl.mcp.server import McpServer
    from stackowl.tools.registry import ToolRegistry

    typer.echo(f"Starting MCP server (transport={transport})")
    server = McpServer(ToolRegistry())
    if transport == "stdio":
        asyncio.run(server.start_stdio())
    else:
        asyncio.run(server.start_sse())


@mcp_app.command("status")
def mcp_status() -> None:
    """Show MCP server status."""
    typer.echo("MCP server status: not running (no persistent process tracked)")


@mcp_app.command("list-clients")
def mcp_list_clients() -> None:
    """List currently connected MCP clients."""
    typer.echo("No active MCP clients")


# ---------------------------------------------------------------------------
# Plugin management commands
# ---------------------------------------------------------------------------


@plugins_app.callback()
def plugins() -> None:
    """Plugin management."""


@plugins_app.command("list")
def plugins_list() -> None:
    """List installed plugins."""
    from stackowl.db.pool import default_db_path
    from stackowl.plugins.registry import PluginRegistry

    registry = PluginRegistry(default_db_path())
    installed = registry.list()
    if not installed:
        typer.echo("No plugins installed")
        return
    for p in installed:
        typer.echo(f"  {p.name}  {p.version}  [{p.type}]")


@plugins_app.command("install")
def plugins_install(
    source: str = typer.Argument(..., help="URL, local path, or index name"),
) -> None:
    """Install a plugin from a URL, local path, or index name."""
    import sys
    from pathlib import Path

    from stackowl.plugins.index import PluginIndex

    # 1. ENTRY
    log.debug("[plugins] plugins_install: entry — source=%s", source)

    # 2. DECISION — check if local path exists first
    local = Path(source)
    if local.exists():
        log.debug("[plugins] plugins_install: decision — local path detected")
        typer.echo(f"Installing local plugin from {source}...")
        typer.echo(
            "Local plugin install via LocalPluginLoader: not yet wired to CLI (use Python API)"
        )
        return

    # 3. STEP — check index for known plugin name
    index = PluginIndex()
    entry = index.lookup(source)
    if entry is None:
        log.debug("[plugins] plugins_install: plugin not in index — source=%s", source)
        typer.echo(f"Plugin '{source}' not found in local index.", err=True)
        typer.echo("Run: stackowl plugins update-index", err=True)
        sys.exit(1)

    # 4. EXIT (remote install deferred)
    typer.echo(f"Found '{source}' in index: {entry.url}")
    typer.echo("Remote install not yet implemented — manual install via local path required")


@plugins_app.command("update-index")
def plugins_update_index(
    url: str | None = typer.Option(None, "--url", help="Override index URL"),
) -> None:
    """Update the local plugin index from the configured source."""
    import sys

    from stackowl.plugins.index import _CONFIG_BASE, PluginIndex

    # 1. ENTRY
    log.debug("[plugins] plugins_update_index: entry — url=%s", url)

    target = _CONFIG_BASE / "plugin-index.yaml"

    if url is not None:
        # 2. DECISION — validate HTTPS only
        if not url.startswith("https://"):
            log.warning("[plugins] plugins_update_index: non-HTTPS URL rejected — url=%s", url)
            typer.echo(
                "Only HTTPS URLs are allowed for plugin index updates",
                err=True,
            )
            sys.exit(1)
        typer.echo(f"Fetching plugin index from {url}...")
        typer.echo(
            "Remote fetch not yet implemented — "
            "set up a local index at ~/.stackowl/plugin-index.yaml"
        )
        return

    # 3. STEP — read and display local index
    typer.echo(f"Plugin index location: {target}")
    index = PluginIndex(target)
    entries = index.all()
    if not entries:
        log.debug("[plugins] plugins_update_index: no entries in local index")
        typer.echo("No plugins in index. Add entries to ~/.stackowl/plugin-index.yaml")
    else:
        # 4. EXIT
        typer.echo(f"{len(entries)} plugin(s) in local index:")
        for e in entries:
            typer.echo(f"  {e.name}  {e.version}  [{e.type}]  — {e.description}")


@plugins_app.command("uninstall")
def plugins_uninstall(
    name: str = typer.Argument(..., help="Plugin name to uninstall"),
) -> None:
    """Uninstall a plugin by name."""
    import asyncio

    from stackowl.db.pool import default_db_path
    from stackowl.plugins.registry import PluginRegistry

    confirm = typer.confirm(f"Uninstall plugin '{name}'?")
    if not confirm:
        typer.echo("Cancelled")
        return
    registry = PluginRegistry(default_db_path())
    asyncio.run(registry.uninstall(name))
    typer.echo(f"✓ Plugin '{name}' uninstalled")


# ---------------------------------------------------------------------------
# Integration management commands
# ---------------------------------------------------------------------------


@integrations_app.callback()
def integrations() -> None:
    """External integration management."""


@app.command()
def setup(
    minimal: bool = typer.Option(False, "--minimal", help="3-step minimal setup: provider, API key, test."),
    channel: str | None = typer.Option(None, "--channel", help="Set up a delivery channel (e.g. telegram)."),
    demo: bool = typer.Option(False, "--demo", help="Non-interactive demo mode — no API key required."),
) -> None:
    """Interactive setup: --minimal, --channel <name>, or --demo."""
    import asyncio

    from stackowl.setup.channel import ChannelSetup
    from stackowl.setup.demo import DemoSetup
    from stackowl.setup.minimal import MinimalSetup

    # 1. ENTRY
    log.debug(
        "[cli] setup: entry — minimal=%s channel=%s demo=%s",
        minimal,
        channel,
        demo,
    )

    # 2. DECISION — route to the appropriate flow
    if demo:
        log.debug("[cli] setup: routing to DemoSetup")
        DemoSetup().run()
    elif channel is not None:
        log.debug("[cli] setup: routing to ChannelSetup channel=%s", channel)
        if channel == "telegram":
            asyncio.run(ChannelSetup().run_telegram())
        else:
            typer.echo(f"✗ Unknown channel: {channel!r}. Supported: telegram", err=True)
            raise typer.Exit(1)
    else:
        # Default — minimal is the implied mode when no flag given
        log.debug("[cli] setup: routing to MinimalSetup")
        asyncio.run(MinimalSetup().run())

    # 4. EXIT
    log.debug("[cli] setup: exit")


@integrations_app.command("health")
def integrations_health() -> None:
    """Show health status for all registered integrations."""
    import asyncio
    import sys

    from stackowl.integrations.registry import IntegrationRegistry

    # 1. ENTRY
    log.debug("[integrations] integrations_health: entry")

    registry = IntegrationRegistry.instance()
    adapters = registry.list_all()
    if not adapters:
        typer.echo("No integrations registered.")
        log.debug("[integrations] integrations_health: exit — no adapters")
        return

    # 2. DECISION — check each adapter's health
    async def _check() -> list[tuple[str, str, str | None]]:
        results = []
        for adapter in adapters:
            try:
                status = await adapter.health_check()
                results.append((status.name, status.status, status.message))
            except Exception as exc:
                log.warning(
                    "[integrations] integrations_health: adapter check failed — %s: %s",
                    adapter.service_name,
                    exc,
                )
                results.append((adapter.service_name, "down", str(exc)))
        return results

    # 3. STEP — run health checks
    statuses = asyncio.run(_check())
    worst = "ok"
    for name, status, message in statuses:
        icon = "✓" if status == "ok" else ("⚠" if status == "degraded" else "✗")
        msg = f"  — {message}" if message else ""
        typer.echo(f"{icon}  {name:<30} {status:<12}{msg}")
        if status == "down":
            worst = "down"
        elif status == "degraded" and worst == "ok":
            worst = "degraded"

    # 4. EXIT — set appropriate exit code
    log.debug("[integrations] integrations_health: exit", extra={"_fields": {"worst": worst}})
    if worst == "degraded":
        sys.exit(1)
    elif worst == "down":
        sys.exit(2)


# ---------------------------------------------------------------------------
# Service management commands
# ---------------------------------------------------------------------------


@app.command("install-service")
def install_service(
    user: bool = typer.Option(False, "--user", help="Install as user-level service (where supported)"),
) -> None:
    """Install StackOwl as a native OS service."""
    from stackowl.service.installer import ServiceInstaller

    # 1. ENTRY
    log.debug("[cli] install_service: entry — user=%s", user)

    # 2. DECISION / 3. STEP — delegate to ServiceInstaller
    ServiceInstaller(user_mode=user).install()

    # 4. EXIT — ServiceInstaller prints its own confirmation
    log.debug("[cli] install_service: exit")


@app.command()
def stop() -> None:
    """Stop a running StackOwl instance by sending SIGTERM to its PID file."""
    import os
    import signal
    import sys
    import time

    from stackowl.service.pid_manager import PidManager

    # 1. ENTRY
    log.debug("[cli] stop: entry")

    pid_manager = PidManager()
    path = pid_manager.pid_path

    # 2. DECISION — check if PID file exists
    if not path.exists():
        typer.echo("StackOwl is not running (no PID file found)", err=True)
        log.debug("[cli] stop: exit — no PID file at %s", path)
        raise typer.Exit(1)

    text = path.read_text(encoding="utf-8").strip()
    try:
        pid = int(text)
    except ValueError:
        typer.echo(f"Invalid PID file contents: {text!r}", err=True)
        log.warning("[cli] stop: invalid PID file contents — %s", text)
        raise typer.Exit(1)

    log.debug("[cli] stop: decision — sending SIGTERM to pid=%d", pid)
    typer.echo(f"Sending SIGTERM to PID {pid}...")

    # 3. STEP — send signal
    try:
        if sys.platform == "win32":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        typer.echo(f"Process {pid} is not running — removing stale PID file")
        pid_manager.release()
        raise typer.Exit(0)
    except PermissionError as exc:
        log.warning("[cli] stop: permission denied sending signal — %s", exc)
        typer.echo(f"Error: permission denied (are you the process owner?): {exc}", err=True)
        raise typer.Exit(1)

    # Wait up to 5s for process to exit
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            typer.echo("✓ StackOwl stopped")
            log.info("[cli] stop: exit — process %d stopped", pid)
            raise typer.Exit(0)
        time.sleep(0.2)

    # 4. EXIT — process still alive after 5s
    typer.echo(f"Warning: process {pid} did not stop within 5 seconds")
    log.warning("[cli] stop: exit — process %d still alive after 5s", pid)
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Export / Import / Backup / Restore commands
# ---------------------------------------------------------------------------


@app.command("export")
def export_cmd(
    output: Path | None = typer.Option(None, "--output", "-o", help="Output path for export archive"),
) -> None:
    """Export StackOwl state to a portable archive."""
    import asyncio
    import sys

    from stackowl.config.test_mode import TestModeGuard, TestModeViolation
    from stackowl.db.pool import DbPool, default_db_path
    from stackowl.export.exporter import Exporter

    # 1. ENTRY
    log.debug("[cli] export_cmd: entry — output=%s", output)

    try:
        TestModeGuard.assert_not_test_mode("export")
    except TestModeViolation as exc:
        log.warning("[cli] export_cmd: blocked in test mode: %s", exc)
        typer.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    async def _run() -> Path:
        db = DbPool(default_db_path())
        await db.open()
        try:
            exporter = Exporter(db=db)
            return await exporter.export(output_path=output)
        finally:
            await db.close()

    # 2. DECISION / 3. STEP — run export
    try:
        result_path = asyncio.run(_run())
    except Exception as exc:
        log.warning("[cli] export_cmd: export failed — %s", exc)
        typer.echo(f"✗ Export failed: {exc}", err=True)
        sys.exit(1)

    # 4. EXIT
    log.info("[cli] export_cmd: exit — path=%s", result_path)
    typer.echo(f"✓ Export written to {result_path}")


@app.command("import")
def import_cmd(
    archive: Path = typer.Argument(..., help="Path to export archive"),
    merge: bool = typer.Option(False, "--merge", help="Merge imported data instead of replacing"),
) -> None:
    """Import StackOwl state from an export archive."""
    import asyncio
    import sys

    from stackowl.db.pool import DbPool, default_db_path
    from stackowl.export.importer import Importer

    # 1. ENTRY
    log.debug("[cli] import_cmd: entry — archive=%s merge=%s", archive, merge)

    if not archive.exists():
        typer.echo(f"✗ Archive not found: {archive}", err=True)
        sys.exit(1)

    async def _run() -> None:
        db = DbPool(default_db_path())
        await db.open()
        try:
            importer = Importer(db=db)
            await importer.run(archive_path=archive, merge=merge)
        finally:
            await db.close()

    # 2. DECISION / 3. STEP — run import
    try:
        asyncio.run(_run())
    except Exception as exc:
        log.warning("[cli] import_cmd: import failed — %s", exc)
        typer.echo(f"✗ Import failed: {exc}", err=True)
        sys.exit(1)

    # 4. EXIT
    log.info("[cli] import_cmd: exit — archive=%s", archive)
    typer.echo("✓ Import complete")


@app.command()
def backup(
    output: Path | None = typer.Option(None, "--output", "-o", help="Output directory for backup"),
) -> None:
    """Create an atomic backup of all StackOwl data stores."""
    import sys

    from stackowl.config.test_mode import TestModeGuard, TestModeViolation
    from stackowl.db.pool import default_db_path
    from stackowl.export.backup import BackupManager

    # 1. ENTRY
    log.debug("[cli] backup: entry — output=%s", output)

    try:
        TestModeGuard.assert_not_test_mode("export")
    except TestModeViolation as exc:
        log.warning("[cli] backup: blocked in test mode: %s", exc)
        typer.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    # 2. DECISION / 3. STEP — create backup
    try:
        manager = BackupManager(db_path=default_db_path())
        result_dir = manager.backup(output_dir=output)
    except Exception as exc:
        log.warning("[cli] backup: failed — %s", exc)
        typer.echo(f"✗ Backup failed: {exc}", err=True)
        sys.exit(1)

    # 4. EXIT
    log.info("[cli] backup: exit — dir=%s", result_dir)
    typer.echo(f"✓ Backup written to {result_dir}")


@app.command()
def restore(
    backup_path: Path = typer.Argument(..., help="Path to the backup directory to restore from"),
) -> None:
    """Restore from a backup directory."""
    import sys

    from stackowl.config.test_mode import TestModeGuard, TestModeViolation
    from stackowl.db.pool import default_db_path
    from stackowl.export.backup import BackupManager

    # 1. ENTRY
    log.debug("[cli] restore: entry — backup_path=%s", backup_path)

    try:
        TestModeGuard.assert_not_test_mode("export")
    except TestModeViolation as exc:
        log.warning("[cli] restore: blocked in test mode: %s", exc)
        typer.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    if not backup_path.exists():
        typer.echo(f"✗ Backup directory not found: {backup_path}", err=True)
        sys.exit(1)

    confirm = typer.prompt("Restore will replace the current database. Type YES to confirm")
    if confirm != "YES":
        typer.echo("Restore cancelled")
        raise typer.Exit(0)

    # 2. DECISION / 3. STEP — run restore
    try:
        manager = BackupManager(db_path=default_db_path())
        manager.restore(backup_path)
    except Exception as exc:
        log.warning("[cli] restore: failed — %s", exc)
        typer.echo(f"✗ Restore failed: {exc}", err=True)
        sys.exit(1)

    # 4. EXIT
    log.info("[cli] restore: exit — backup_path=%s", backup_path)
    typer.echo("✓ Restore complete")
