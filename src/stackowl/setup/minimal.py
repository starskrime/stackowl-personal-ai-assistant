"""MinimalSetup — interactive onboarding: provider, API key, test, Telegram."""

from __future__ import annotations

import dataclasses
import getpass
import time

import typer

from stackowl.config.secret_writer import store_secret
from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.setup.localize import localize
from stackowl.setup.provider_catalog import ProviderCatalog, ProviderEntry


def _store_secret(service_name: str, secret: str) -> tuple[str, str]:
    """Persist *secret* via the shared writer; returns ``(description, yaml_ref)``.

    Thin delegate to :func:`stackowl.config.secret_writer.store_secret` so the
    setup flow and the ``/provider`` command share one implementation (DRY).
    Behaviour is identical: keyring first, mode-0600 file fallback.
    """
    return store_secret(service_name, secret)


def _test_provider_connection(entry: ProviderEntry, api_key: str) -> bool:
    """Send a lightweight request to verify the key/endpoint works.

    Dispatches by entry.protocol — no branching on provider names.
    Returns True if reachable; False on any error.
    """
    # 1. ENTRY
    log.setup.debug(
        "[minimal] _test_provider_connection: entry",
        extra={"_fields": {"provider": entry.name, "protocol": entry.protocol}},
    )

    try:
        import httpx

        # 2. DECISION — per-protocol test endpoint
        if entry.protocol == "anthropic":
            resp = httpx.post(
                f"{entry.base_url}/messages",
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
            # 400 = wrong model but key is valid
            ok = resp.status_code in (200, 400)

        elif entry.protocol == "openai":
            if entry.is_local:
                # Local servers: just check the /models endpoint, no auth
                resp = httpx.get(f"{entry.base_url}/models", timeout=5.0)
            else:
                resp = httpx.get(
                    f"{entry.base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0,
                )
            ok = resp.status_code == 200

        elif entry.protocol == "gemini":
            resp = httpx.get(
                f"{entry.base_url}/models",
                params={"key": api_key},
                timeout=10.0,
            )
            ok = resp.status_code == 200

        elif entry.protocol == "grok":
            resp = httpx.get(
                f"{entry.base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            ok = resp.status_code == 200

        else:
            log.setup.debug(
                "[minimal] _test_provider_connection: unknown protocol — skipping test",
                extra={"_fields": {"protocol": entry.protocol}},
            )
            return True

    except Exception as exc:  # noqa: BLE001
        log.setup.warning(
            "[minimal] _test_provider_connection: connection test failed",
            extra={"_fields": {"provider": entry.name, "reason": str(exc)}},
        )
        return False

    # 4. EXIT
    log.setup.debug(
        "[minimal] _test_provider_connection: exit",
        extra={"_fields": {"provider": entry.name, "ok": ok}},
    )
    return ok


class MinimalSetup:
    """Interactive onboarding: choose provider, enter API key, verify, configure Telegram."""

    async def run(self) -> None:
        """Execute the full setup flow."""
        # 1. ENTRY
        log.setup.info("[minimal] MinimalSetup.run: entry")
        t0 = time.monotonic()

        # Step 1 — load and display provider catalog
        entries = ProviderCatalog.load()
        entry = self._choose_provider(entries)
        log.setup.debug(
            "[minimal] MinimalSetup.run: provider selected",
            extra={"_fields": {"provider": entry.name, "protocol": entry.protocol}},
        )

        # Step 2 — choose model (or enter a custom one)
        chosen_model = self._choose_model(entry)
        log.setup.debug(
            "[minimal] MinimalSetup.run: model selected",
            extra={"_fields": {"provider": entry.name, "model": chosen_model}},
        )

        # 3. STEP — base URL (always offered; pressing Enter keeps the bundled default)
        default_url = entry.base_url or ""
        if default_url:
            raw_url = typer.prompt("Base URL", default=default_url).strip()
        else:
            raw_url = typer.prompt(
                "Base URL (e.g. https://my-api.example.com/v1)"
            ).strip()
        base_url_override: str | None = (
            raw_url if raw_url and raw_url != entry.base_url else None
        )
        log.setup.debug(
            "[minimal] MinimalSetup.run: base URL determined",
            extra={"_fields": {"provider": entry.name, "overridden": base_url_override is not None}},
        )

        # 4. DECISION — skip API key prompt for local providers
        api_key = ""
        api_key_ref = ""
        if not entry.needs_api_key or entry.is_local:
            typer.echo(f"  {entry.label} runs locally — no API key required.")
        else:
            if entry.key_url:
                typer.echo(f"  Get a key at: {entry.key_url}")
            api_key = getpass.getpass(f"API key for {entry.label}: ")

        # 5. STEP — store secret and write config
        if api_key:
            storage_loc, api_key_ref = _store_secret(entry.name, api_key)
            typer.echo(f"  ✓ Key stored ({storage_loc})")

        self._write_config(entry, api_key_ref, base_url_override, chosen_model or None)

        # 6. STEP — connection test (uses effective URL, which may be overridden)
        effective_entry = (
            dataclasses.replace(entry, base_url=base_url_override)
            if base_url_override
            else entry
        )
        typer.echo(f"  Testing connection to {entry.label}…")
        ok = _test_provider_connection(effective_entry, api_key)
        if ok:
            typer.echo("  ✓ Connection verified")
        else:
            typer.echo(
                "  ⚠ Connection test failed — check your API key and network. "
                "You can retry with: stackowl setup --minimal"
            )

        # 6. STEP — optional Telegram setup
        self._setup_telegram()

        # 7. STEP — record completion event so stackowl start won't re-prompt
        await self._record_completion()

        # 8. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        typer.echo(localize("setup_ready"))
        typer.echo(
            f"\nTip: drop YAML files in {StackowlHome.providers_dir()} to add your own providers."
        )
        log.setup.info(
            "[minimal] MinimalSetup.run: exit",
            extra={"_fields": {"provider": entry.name, "duration_ms": duration_ms}},
        )

    # -- provider selection ----------------------------------------------------

    @staticmethod
    def _choose_provider(entries: list[ProviderEntry]) -> ProviderEntry:
        """Display grouped provider list and return the chosen entry."""
        from stackowl.setup.provider_catalog import ProviderCatalog

        typer.echo("\nChoose a provider:\n")

        # Group by protocol for legibility
        seen_protocols: list[str] = []
        numbered: list[ProviderEntry] = []

        for entry in entries:
            if entry.is_local:
                group = "Local"
            elif entry.name == "custom":
                group = "Other"
            else:
                group = ProviderCatalog.protocol_label(entry.protocol)

            if group not in seen_protocols:
                seen_protocols.append(group)
                typer.echo(f"  {group}:")

            idx = len(numbered) + 1
            tier_tag = f" ({entry.tier})" if entry.tier == "fast" else ""
            numbered.append(entry)
            typer.echo(f"    [{idx:2d}] {entry.label}{tier_tag}")

        typer.echo("")
        raw = typer.prompt(f"Choose [1-{len(numbered)}]", default="1")
        try:
            idx = int(raw.strip()) - 1
            if not 0 <= idx < len(numbered):
                raise ValueError("out of range")
            return numbered[idx]
        except ValueError:
            typer.echo("Invalid choice — defaulting to Anthropic")
            log.setup.warning("[minimal] MinimalSetup._choose_provider: invalid choice")
            return numbered[0]

    # -- model selection -------------------------------------------------------

    @staticmethod
    def _choose_model(entry: ProviderEntry) -> str:
        """Show the provider's known model roster and return the chosen model name.

        The user may pick from the numbered list OR type any model name directly.
        For providers with no known model list (lmstudio, custom) only the
        free-text path is offered.
        """
        # 1. ENTRY
        log.setup.debug(
            "[minimal] MinimalSetup._choose_model: entry",
            extra={"_fields": {"provider": entry.name, "model_count": len(entry.models)}},
        )

        models = list(entry.models)

        if not models:
            # 2a. DECISION — no known list; just prompt for a name
            default = entry.default_model or ""
            prompt_text = "Model name"
            if default:
                return typer.prompt(prompt_text, default=default).strip() or default
            result = typer.prompt(prompt_text).strip()
            log.setup.debug(
                "[minimal] MinimalSetup._choose_model: exit — free-text",
                extra={"_fields": {"model": result}},
            )
            return result

        # 2b. DECISION — display numbered list + custom option
        typer.echo(f"\n  Models for {entry.label}:")
        for i, m in enumerate(models, 1):
            marker = " (default)" if m == entry.default_model else ""
            typer.echo(f"    [{i:2d}] {m}{marker}")
        custom_idx = len(models) + 1
        typer.echo(f"    [{custom_idx:2d}] Enter model name manually")
        typer.echo("")

        default_idx = (models.index(entry.default_model) + 1) if entry.default_model in models else 1
        raw = typer.prompt(f"  Choose [1-{custom_idx}]", default=str(default_idx)).strip()

        try:
            idx = int(raw) - 1
            if idx == len(models):
                # User chose "enter manually"
                result = typer.prompt("  Model name").strip()
            elif 0 <= idx < len(models):
                result = models[idx]
            else:
                raise ValueError("out of range")
        except ValueError:
            typer.echo(f"  Invalid choice — using default '{entry.default_model}'")
            result = entry.default_model

        # 4. EXIT
        log.setup.debug(
            "[minimal] MinimalSetup._choose_model: exit",
            extra={"_fields": {"provider": entry.name, "model": result}},
        )
        return result

    # -- config write ----------------------------------------------------------

    def _write_config(
        self,
        entry: ProviderEntry,
        api_key_ref: str,
        base_url_override: str | None = None,
        model_override: str | None = None,
    ) -> None:
        """Merge provider entry into ~/.stackowl/stackowl.yaml."""
        # 1. ENTRY
        log.setup.debug(
            "[minimal] MinimalSetup._write_config: entry",
            extra={"_fields": {"provider": entry.name}},
        )
        try:
            from stackowl.setup.yaml_writer import write_provider_config

            write_provider_config(
                StackowlHome.config_file(),
                entry,
                api_key_ref,
                base_url_override=base_url_override,
                default_model_override=model_override,
            )
            typer.echo(f"  ✓ Config written to {StackowlHome.config_file()}")
            log.setup.info(
                "[minimal] MinimalSetup._write_config: exit — written",
                extra={"_fields": {"provider": entry.name}},
            )
        except Exception as exc:
            log.setup.warning(
                "[minimal] MinimalSetup._write_config: could not write config — %s", exc
            )
            typer.echo(f"  ⚠ Could not write config: {exc}")

    # -- Telegram setup --------------------------------------------------------

    def _setup_telegram(self) -> None:
        """Optionally collect Telegram bot token and allowed user ID."""
        # 1. ENTRY
        log.setup.debug("[minimal] MinimalSetup._setup_telegram: entry")

        typer.echo("\n--- Telegram (optional) ---")
        typer.echo("Skip this step if you don't use Telegram. Press Enter to skip each field.")

        # 2. DECISION — prompt for bot token
        bot_token = typer.prompt("Telegram bot token", default="", show_default=False)
        bot_token = bot_token.strip()
        if not bot_token:
            typer.echo("  Skipping Telegram setup.")
            log.setup.debug("[minimal] MinimalSetup._setup_telegram: skipped — no token entered")
            return

        # 3. STEP — store bot token as secret
        storage_loc, bot_token_ref = _store_secret("telegram-bot", bot_token)
        typer.echo(f"  ✓ Bot token stored ({storage_loc})")

        raw_uid = typer.prompt(
            "Your Telegram user ID (numeric, e.g. 123456789)",
            default="",
            show_default=False,
        ).strip()

        allowed_ids: list[int] = []
        if raw_uid:
            try:
                allowed_ids = [int(raw_uid)]
            except ValueError:
                typer.echo(f"  ⚠ '{raw_uid}' is not a valid numeric user ID — skipping")
                log.setup.warning(
                    "[minimal] MinimalSetup._setup_telegram: invalid user ID — %s", raw_uid
                )

        # 4. EXIT — write channel config
        self._write_telegram_config(bot_token_ref, allowed_ids)
        log.setup.debug(
            "[minimal] MinimalSetup._setup_telegram: exit",
            extra={"_fields": {"allowed_ids_count": len(allowed_ids)}},
        )

    def _write_telegram_config(self, bot_token_ref: str, allowed_ids: list[int]) -> None:
        """Merge Telegram channel settings into ~/.stackowl/stackowl.yaml."""
        log.setup.debug("[minimal] MinimalSetup._write_telegram_config: entry")
        try:
            from stackowl.setup.yaml_writer import write_channel_config

            channel_data: dict[str, object] = {"bot_token": bot_token_ref}
            if allowed_ids:
                channel_data["allowed_user_ids"] = allowed_ids

            write_channel_config(StackowlHome.config_file(), "telegram_channel", channel_data)
            typer.echo("  ✓ Telegram config written")
            log.setup.info("[minimal] MinimalSetup._write_telegram_config: exit — written")
        except Exception as exc:
            log.setup.warning(
                "[minimal] MinimalSetup._write_telegram_config: could not write config — %s", exc
            )
            typer.echo(f"  ⚠ Could not write Telegram config: {exc}")

    # -- completion recording --------------------------------------------------

    async def _record_completion(self) -> None:
        """Record minimal_setup_complete so stackowl start won't re-prompt."""
        try:
            from stackowl.db.pool import DbPool
            from stackowl.setup.onboarding_table import OnboardingTable

            pool = DbPool(StackowlHome.db_path())
            await pool.open()
            try:
                await OnboardingTable.record_event(pool, "minimal_setup_complete")
            finally:
                await pool.close()
            log.setup.debug("[minimal] MinimalSetup._record_completion: event recorded")
        except Exception as exc:
            log.setup.warning(
                "[minimal] MinimalSetup._record_completion: could not record event — %s", exc
            )
