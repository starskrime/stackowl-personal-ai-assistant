"""providers_cli — Typer subgroup for ``stackowl providers`` commands.

Provides:
  stackowl providers                — list configured providers
  stackowl providers enable <name>  — flip enabled=true in stackowl.yaml
  stackowl providers disable <name> — flip enabled=false in stackowl.yaml
"""

from __future__ import annotations

import dataclasses
import getpass as _getpass
import sys
from pathlib import Path
from typing import Any

import typer
from ruamel.yaml import YAML

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome

providers_app = typer.Typer(help="Manage AI providers.")


def _config_path() -> Path:
    return StackowlHome.config_file()


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
    typer.echo(f"  {'NAME':<20} {'PROTOCOL':<12} {'TIERS':<20} STATUS")
    for prov in settings.providers:
        status = "enabled" if prov.enabled else "disabled"
        typer.echo(f"  {prov.name:<20} {prov.protocol:<12} {','.join(prov.tiers):<20} {status}")
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


# ── private helpers ────────────────────────────────────────────────────────────


def _choose_tier(default: str) -> str:
    """Prompt the user to pick a tier from the registry's canonical order."""
    from stackowl.providers.registry import _TIER_ORDER

    tiers = list(_TIER_ORDER)
    typer.echo(f"\n  Tier (current: {default}):")
    for i, t in enumerate(tiers, 1):
        marker = " ← current" if t == default else ""
        typer.echo(f"    [{i}] {t}{marker}")
    typer.echo("")
    default_idx = tiers.index(default) + 1 if default in tiers else 1
    raw = typer.prompt(f"  Choose [1-{len(tiers)}]", default=str(default_idx)).strip()
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(tiers):
            return tiers[idx]
    except ValueError:
        pass
    return default


def _find_provider_raw(name: str) -> dict[str, Any] | None:
    """Return the raw YAML dict for the named provider, or None if not found."""
    data = _load(_config_path())
    for entry in data.get("providers") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    return None


def _delete_secret_if_owned(api_key_ref: str) -> None:
    """Offer to delete a file: or keychain: secret after provider removal."""
    if not api_key_ref:
        return
    if api_key_ref.startswith("file:"):
        secret_path = Path(api_key_ref[len("file:"):])
        if not secret_path.exists():
            typer.echo("  (secret file not found — skipping)")
            return
        typer.echo(f"  Secret file: {secret_path}")
        if typer.confirm("  Also delete the stored secret?", default=True):
            try:
                secret_path.unlink()
                typer.echo("  ✓ Secret file deleted")
            except OSError as exc:
                log.cli.warning("[cli] _delete_secret_if_owned: unlink failed", extra={"_fields": {"error": str(exc)}})
                typer.echo(f"  ⚠ Could not delete secret file: {exc}", err=True)
    elif api_key_ref.startswith("keychain:"):
        service = api_key_ref[len("keychain:"):]
        typer.echo(f"  Keychain entry: {service}")
        if typer.confirm("  Also delete the stored secret?", default=True):
            try:
                import keyring
                keyring.delete_password(service, service)
                typer.echo("  ✓ Keychain entry deleted")
            except Exception as exc:  # noqa: BLE001
                log.cli.warning(
                    "[cli] _delete_secret_if_owned: keyring delete failed",
                    extra={"_fields": {"error": str(exc)}},
                )
                typer.echo(f"  ⚠ Could not delete keychain entry: {exc}", err=True)


# ── new commands ───────────────────────────────────────────────────────────────


@providers_app.command("add")
def providers_add() -> None:
    """Add a new AI provider interactively."""
    log.cli.debug("[cli] providers_add: entry")
    from stackowl.setup.minimal import MinimalSetup, _store_secret, _test_provider_connection
    from stackowl.setup.provider_catalog import ProviderCatalog
    from stackowl.setup.yaml_writer import update_provider_field, write_provider_config

    config_path = _config_path()

    # 1. Pick provider → model → URL → tier
    entries = ProviderCatalog.load()
    entry = MinimalSetup._choose_provider(entries)
    model = MinimalSetup._choose_model(entry)
    default_url = entry.base_url or ""
    if default_url:
        url = typer.prompt("Base URL", default=default_url).strip()
    else:
        url = typer.prompt("Base URL (e.g. http://localhost:11434/v1)").strip()
    tier = _choose_tier(default=entry.tier)

    # 2. Confirm overwrite if already configured
    existing = _find_provider_raw(entry.name)
    if existing:
        typer.echo(f"  Provider '{entry.name}' is already configured.")
        if not typer.confirm("  Overwrite it?", default=False):
            typer.echo("  Aborted.")
            raise typer.Exit(0)

    # 3. API key
    api_key = ""
    api_key_ref = ""
    if entry.is_local or not entry.needs_api_key:
        typer.echo(f"  {entry.label} runs locally — no API key required.")
    else:
        if entry.key_url:
            typer.echo(f"  Get a key at: {entry.key_url}")
        api_key = _getpass.getpass(f"API key for {entry.label}: ")
        if api_key:
            storage_loc, api_key_ref = _store_secret(entry.name, api_key)
            typer.echo(f"  ✓ Key stored ({storage_loc})")

    # 4. Write config; patch tier if user changed it from catalog default
    base_url_override = url if url and url != entry.base_url else None
    model_override = model if model != entry.default_model else None
    write_provider_config(config_path, entry, api_key_ref,
                          base_url_override=base_url_override,
                          default_model_override=model_override)
    if tier != entry.tier:
        update_provider_field(config_path, entry.name, "tiers", [tier])
    typer.echo(f"  ✓ Provider '{entry.name}' written to config")

    # 5. Connection test
    effective_entry = dataclasses.replace(entry, base_url=url or entry.base_url)
    typer.echo(f"  Testing connection to {entry.label}…")
    ok = _test_provider_connection(effective_entry, api_key)
    typer.echo("  ✓ Connection verified" if ok else
               "  ⚠ Connection test failed — check base_url and API key")
    log.cli.info(
        "[cli] providers_add: exit",
        extra={"_fields": {"name": entry.name, "tier": tier, "ok": ok}},
    )


@providers_app.command("remove")
def providers_remove(
    name: str = typer.Argument(..., help="Provider name to remove."),
) -> None:
    """Remove a configured provider from stackowl.yaml."""
    log.cli.debug("[cli] providers_remove: entry", extra={"_fields": {"name": name}})
    from stackowl.setup.yaml_writer import remove_provider_config

    config_path = _config_path()
    raw = _find_provider_raw(name)
    if raw is None:
        typer.echo(f"✗ Provider not found: {name}", err=True)
        raise typer.Exit(1)

    typer.echo(f"  {name}  tiers={raw.get('tiers', ['?'])}  base_url={raw.get('base_url', '?')}")
    if not typer.confirm(f"Remove provider '{name}'?", default=False):
        typer.echo("  Aborted.")
        raise typer.Exit(0)

    removed = remove_provider_config(config_path, name)
    if not removed:
        typer.echo(f"✗ Failed to remove '{name}'", err=True)
        raise typer.Exit(1)
    typer.echo(f"  ✓ Provider '{name}' removed")

    api_key_ref = str(raw.get("api_key") or "")
    if api_key_ref:
        _delete_secret_if_owned(api_key_ref)

    log.cli.info("[cli] providers_remove: exit", extra={"_fields": {"name": name}})


@providers_app.command("edit")
def providers_edit(
    name: str = typer.Argument(..., help="Provider name to edit."),
) -> None:
    """Edit tier, base_url, default_model, or rate limit for a configured provider."""
    log.cli.debug("[cli] providers_edit: entry", extra={"_fields": {"name": name}})
    from stackowl.config.settings import Settings
    from stackowl.setup.yaml_writer import update_provider_field

    config_path = _config_path()
    try:
        settings = Settings()
    except Exception as exc:
        typer.echo(f"✗ Failed to load settings: {exc}", err=True)
        raise typer.Exit(1) from exc

    prov = next((p for p in settings.providers if p.name == name), None)
    if prov is None:
        typer.echo(f"✗ Provider not found: {name}", err=True)
        raise typer.Exit(1)

    current_tier = prov.tiers[0]
    typer.echo(f"\n  Editing: {name}")
    typer.echo(f"  tiers={list(prov.tiers)}  base_url={prov.base_url or '(none)'}  "
               f"model={prov.default_model}  rate_limit={prov.rate_limit_rpm or 'unlimited'}")

    new_tier = _choose_tier(default=current_tier)
    new_base_url = typer.prompt("Base URL", default=prov.base_url or "").strip()
    new_model = typer.prompt("Default model", default=prov.default_model).strip()
    raw_limit = typer.prompt(
        "Rate limit rpm (0 = unlimited)", default=str(prov.rate_limit_rpm or 0)
    ).strip()
    try:
        new_rate_limit: int | None = int(raw_limit) or None
    except ValueError:
        new_rate_limit = prov.rate_limit_rpm

    changed: list[str] = []
    if new_tier != current_tier:
        update_provider_field(config_path, name, "tiers", [new_tier])
        changed.append("tiers")
    if new_base_url != (prov.base_url or ""):
        update_provider_field(config_path, name, "base_url", new_base_url or None)
        changed.append("base_url")
    if new_model and new_model != prov.default_model:
        update_provider_field(config_path, name, "default_model", new_model)
        changed.append("default_model")
    if new_rate_limit != prov.rate_limit_rpm:
        update_provider_field(config_path, name, "rate_limit_rpm", new_rate_limit)
        changed.append("rate_limit_rpm")

    typer.echo(f"  ✓ Updated: {', '.join(changed)}" if changed else "  No changes.")
    log.cli.info(
        "[cli] providers_edit: exit",
        extra={"_fields": {"name": name, "changed": changed}},
    )


@providers_app.command("test")
def providers_test(
    name: str = typer.Argument(..., help="Provider name to test."),
) -> None:
    """Test connectivity to a configured provider."""
    log.cli.debug("[cli] providers_test: entry", extra={"_fields": {"name": name}})
    from stackowl.config.secret_resolver import SecretResolver
    from stackowl.config.settings import Settings
    from stackowl.setup.minimal import _test_provider_connection
    from stackowl.setup.provider_catalog import ProviderEntry

    try:
        settings = Settings()
    except Exception as exc:
        typer.echo(f"✗ Failed to load settings: {exc}", err=True)
        raise typer.Exit(1) from exc

    prov = next((p for p in settings.providers if p.name == name), None)
    if prov is None:
        typer.echo(f"✗ Provider not found: {name}", err=True)
        raise typer.Exit(1)

    api_key = ""
    if prov.api_key:
        try:
            api_key = SecretResolver.resolve(prov.api_key)
        except Exception as exc:  # noqa: BLE001
            log.cli.warning("[cli] providers_test: api_key resolution failed", extra={"_fields": {"error": str(exc)}})
            typer.echo(f"  ⚠ Could not resolve API key: {exc}", err=True)

    test_entry = ProviderEntry(
        name=prov.name,
        label=prov.name,
        protocol=prov.protocol,
        base_url=prov.base_url or "",
        default_model=prov.default_model,
        models=(),
        tier=prov.tiers[0],
        needs_api_key=bool(prov.api_key),
        is_local=not bool(prov.api_key),
    )

    typer.echo(f"  Testing {name} at {prov.base_url or '?'}…")
    ok = _test_provider_connection(test_entry, api_key)
    if ok:
        typer.echo(f"  ✓ {name} reachable")
    else:
        typer.echo(f"  ✗ {name} unreachable — check base_url and API key")
        raise typer.Exit(1)

    log.cli.info("[cli] providers_test: exit", extra={"_fields": {"name": name, "ok": ok}})
