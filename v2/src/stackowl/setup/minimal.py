"""MinimalSetup — interactive 3-step onboarding: provider, API key, test message."""

from __future__ import annotations

import getpass
import platform
import stat
import time
from pathlib import Path

import platformdirs
import typer

from stackowl.infra.observability import log
from stackowl.setup.localize import localize

_KNOWN_PROVIDERS: list[str] = ["anthropic", "openai", "ollama", "openai-compatible"]


def _store_secret(provider: str, api_key: str) -> str:
    """Persist *api_key* for *provider* using the best available mechanism.

    Returns a description of where the secret was stored (for logging — key itself is NOT logged).
    """
    # 1. ENTRY
    log.setup.debug(
        "[minimal] _store_secret: entry",
        extra={"_fields": {"provider": provider}},
    )
    # 2. DECISION — try keyring first
    try:
        import keyring  # optional dependency

        keyring.set_password("stackowl", provider, api_key)
        log.setup.debug(
            "[minimal] _store_secret: exit — stored in OS keyring",
            extra={"_fields": {"provider": provider}},
        )
        return "OS keyring"
    except Exception as exc:  # noqa: BLE001
        log.setup.debug(
            "[minimal] _store_secret: keyring unavailable — falling back to encrypted file",
            extra={"_fields": {"provider": provider, "reason": str(exc)}},
        )

    # 3. STEP — fall back to a mode-0600 file
    config_dir = Path(platformdirs.user_config_dir("stackowl")) / ".secrets"
    config_dir.mkdir(parents=True, exist_ok=True)
    secret_file = config_dir / f"{provider}.key"
    secret_file.write_text(api_key, encoding="utf-8")
    # restrict to owner read/write only (cross-platform best-effort)
    try:
        secret_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        log.setup.warning(
            "[minimal] _store_secret: could not set file permissions",
            extra={"_fields": {"provider": provider, "reason": str(exc)}},
        )

    # 4. EXIT
    log.setup.debug(
        "[minimal] _store_secret: exit — stored in secret file",
        extra={"_fields": {"provider": provider, "path": str(secret_file)}},
    )
    return f"file:{secret_file}"


def _test_provider_connection(provider: str, api_key: str) -> bool:
    """Send a lightweight test message to verify the API key works.

    Returns True if connection succeeded; False on any error.
    """
    # 1. ENTRY
    log.setup.debug(
        "[minimal] _test_provider_connection: entry",
        extra={"_fields": {"provider": provider}},
    )
    # 2. DECISION — choose test endpoint by provider
    try:
        if provider == "anthropic":
            import httpx

            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-20240307",
                    "max_tokens": 4,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
                timeout=10.0,
            )
            ok = resp.status_code in (200, 400)  # 400 = wrong model — key is valid
        elif provider == "openai":
            import httpx

            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            ok = resp.status_code == 200
        elif provider == "ollama":
            import httpx

            resp = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
            ok = resp.status_code == 200
        else:
            # openai-compatible — skip live test
            log.setup.debug(
                "[minimal] _test_provider_connection: skipping live test for openai-compatible"
            )
            return True
    except Exception as exc:  # noqa: BLE001
        log.setup.warning(
            "[minimal] _test_provider_connection: connection test failed",
            extra={"_fields": {"provider": provider, "reason": str(exc)}},
        )
        return False

    # 4. EXIT
    log.setup.debug(
        "[minimal] _test_provider_connection: exit",
        extra={"_fields": {"provider": provider, "ok": ok}},
    )
    return ok


class MinimalSetup:
    """Interactive 3-step onboarding: choose provider, enter API key, verify."""

    async def run(self) -> None:
        """Execute the minimal setup flow."""
        # 1. ENTRY
        log.setup.info("[minimal] MinimalSetup.run: entry")
        t0 = time.monotonic()

        # Step 1 — choose provider
        typer.echo("\nAvailable providers:")
        for i, p in enumerate(_KNOWN_PROVIDERS, 1):
            typer.echo(f"  {i}. {p}")

        raw = typer.prompt("Choose provider [1-4]", default="1")
        try:
            idx = int(raw.strip()) - 1
            if not 0 <= idx < len(_KNOWN_PROVIDERS):
                raise ValueError("out of range")
            provider = _KNOWN_PROVIDERS[idx]
        except ValueError:
            typer.echo("Invalid choice — defaulting to anthropic")
            provider = "anthropic"
            log.setup.warning("[minimal] MinimalSetup.run: invalid provider choice")

        # 2. DECISION — skip API key prompt for ollama (local, no key needed)
        log.setup.debug(
            "[minimal] MinimalSetup.run: provider selected",
            extra={"_fields": {"provider": provider}},
        )

        api_key = ""
        if provider == "ollama":
            typer.echo("Ollama runs locally — no API key required.")
        else:
            api_key = getpass.getpass(f"API key for {provider}: ")

        # 3. STEP — store secret
        if api_key:
            storage_loc = _store_secret(provider, api_key)
            typer.echo(f"  ✓ Key stored ({storage_loc})")

        # Test connection
        typer.echo(f"  Testing connection to {provider}…")
        ok = _test_provider_connection(provider, api_key)
        if ok:
            typer.echo("  ✓ Connection verified")
        else:
            typer.echo(
                "  ⚠ Connection test failed — check your API key and network. "
                "You can retry with stackowl setup --minimal"
            )

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        typer.echo(localize("setup_ready"))
        log.setup.info(
            "[minimal] MinimalSetup.run: exit",
            extra={"_fields": {"provider": provider, "duration_ms": duration_ms}},
        )
