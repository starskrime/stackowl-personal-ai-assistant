"""providers_cli — Typer subgroup for ``stackowl providers`` commands.

Provides:
  stackowl providers                — list configured providers
  stackowl providers enable <name>  — flip enabled=true in stackowl.yaml
  stackowl providers disable <name> — flip enabled=false in stackowl.yaml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import typer
from ruamel.yaml import YAML

from stackowl.infra.observability import log

providers_app = typer.Typer(help="Manage AI providers.")


def _config_path() -> Path:
    return Path(os.environ.get("STACKOWL_CONFIG_FILE", "stackowl.yaml"))


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    return y


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            loaded = _yaml().load(fh)
    except Exception as exc:
        log.cli.warning(
            "[cli] providers._load: yaml parse failed",
            extra={"_fields": {"path": str(path), "error": str(exc)}},
        )
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        _yaml().dump(data, fh)


def _set_enabled(name: str, enabled: bool) -> None:
    log.cli.debug(
        "[cli] providers._set_enabled: entry",
        extra={"_fields": {"name": name, "enabled": enabled}},
    )
    path = _config_path()
    if not path.exists():
        typer.echo(f"✗ {path} not found — run stackowl init first", err=True)
        sys.exit(1)
    data = _load(path)
    providers = data.get("providers")
    if not isinstance(providers, list):
        typer.echo("✗ No providers section in stackowl.yaml", err=True)
        sys.exit(1)
    found = False
    for entry in providers:
        if isinstance(entry, dict) and entry.get("name") == name:
            entry["enabled"] = enabled
            found = True
            break
    if not found:
        typer.echo(f"✗ Provider not found: {name}", err=True)
        sys.exit(1)
    _save(path, data)
    log.cli.info(
        "[cli] providers._set_enabled: exit",
        extra={"_fields": {"name": name, "enabled": enabled}},
    )
    state = "enabled" if enabled else "disabled"
    typer.echo(f"✓ Provider '{name}' {state}")


@providers_app.callback(invoke_without_command=True)
def providers_list(ctx: typer.Context) -> None:
    """List all configured providers."""
    if ctx.invoked_subcommand is not None:
        return
    log.cli.debug("[cli] providers_list: entry")
    from stackowl.config.settings import Settings

    try:
        settings = Settings()
    except Exception as exc:
        log.cli.error("[cli] providers_list: settings load failed", exc_info=exc)
        typer.echo(f"✗ Failed to load settings: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not settings.providers:
        typer.echo("No providers configured.")
        return
    typer.echo(f"  {'NAME':<20} {'PROTOCOL':<12} {'TIER':<12} STATUS")
    for prov in settings.providers:
        status = "enabled" if prov.enabled else "disabled"
        typer.echo(f"  {prov.name:<20} {prov.protocol:<12} {prov.tier:<12} {status}")
    log.cli.debug(
        "[cli] providers_list: exit",
        extra={"_fields": {"count": len(settings.providers)}},
    )


@providers_app.command("enable")
def providers_enable(
    name: str = typer.Argument(..., help="Provider name to enable."),
) -> None:
    """Set ``enabled: true`` for the named provider in stackowl.yaml."""
    _set_enabled(name, True)


@providers_app.command("disable")
def providers_disable(
    name: str = typer.Argument(..., help="Provider name to disable."),
) -> None:
    """Set ``enabled: false`` for the named provider in stackowl.yaml."""
    _set_enabled(name, False)
