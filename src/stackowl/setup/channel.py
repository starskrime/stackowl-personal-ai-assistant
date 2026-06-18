"""ChannelSetup — interactive channel onboarding flows."""

from __future__ import annotations

import getpass
import time

import typer

from stackowl.infra.observability import log


class ChannelSetup:
    """Interactive setup for delivery channels (Telegram, etc.)."""

    async def run_telegram(self) -> None:
        """Configure a Telegram bot channel.

        Steps:
        1. Prompt for bot token (masked via getpass).
        2. Validate by calling getMe.
        3. Prompt for allowed user IDs (comma-separated integers).
        4. Save token via keyring / secret file.
        5. Print success.
        """
        # 1. ENTRY
        log.setup.info("[channel] ChannelSetup.run_telegram: entry")
        t0 = time.monotonic()

        # Step 1 — prompt for token (masked)
        bot_token = getpass.getpass("Telegram bot token: ")
        if not bot_token.strip():
            typer.echo("✗ Bot token cannot be empty.", err=True)
            log.setup.warning("[channel] run_telegram: empty bot token — aborting")
            return

        # 2. DECISION — validate token via Telegram API
        log.setup.debug("[channel] run_telegram: validating token with Telegram API")
        username, valid = self._validate_telegram_token(bot_token)
        if not valid:
            typer.echo(
                "✗ Telegram rejected the token — check it at https://t.me/BotFather",
                err=True,
            )
            log.setup.warning("[channel] run_telegram: token validation failed")
            return

        # 3. STEP — prompt for allowed user IDs
        raw_ids = typer.prompt("Allowed user IDs (comma-separated integers)")
        try:
            allowed_ids = [int(uid.strip()) for uid in raw_ids.split(",") if uid.strip()]
            if not allowed_ids:
                raise ValueError("empty list")
        except ValueError as exc:
            typer.echo(f"✗ Invalid user ID list: {exc}", err=True)
            log.setup.warning(
                "[channel] run_telegram: invalid user IDs",
                extra={"_fields": {"raw": raw_ids, "reason": str(exc)}},
            )
            return

        # Store token using same mechanism as MinimalSetup
        from stackowl.setup.minimal import _store_secret

        storage_loc = _store_secret("telegram", bot_token)
        log.setup.debug(
            "[channel] run_telegram: token stored",
            extra={"_fields": {"storage": storage_loc}},
        )

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        typer.echo(
            f"✓ Telegram bot @{username} connected — allowed users: {allowed_ids}"
        )
        log.setup.info(
            "[channel] run_telegram: exit",
            extra={
                "_fields": {
                    "username": username,
                    "allowed_count": len(allowed_ids),
                    "duration_ms": duration_ms,
                }
            },
        )

    def _validate_telegram_token(self, token: str) -> tuple[str, bool]:
        """Call Telegram getMe and return (username, is_valid).

        Returns ("unknown", False) on any network or API error.
        """
        # 1. ENTRY
        log.setup.debug("[channel] _validate_telegram_token: entry")
        try:
            import httpx

            resp = httpx.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=10.0,
            )
            # 2. DECISION — check HTTP and Telegram result
            if resp.status_code != 200:
                log.setup.warning(
                    "[channel] _validate_telegram_token: non-200 response",
                    extra={"_fields": {"status": resp.status_code}},
                )
                return "unknown", False

            data = resp.json()
            if not data.get("ok"):
                log.setup.warning(
                    "[channel] _validate_telegram_token: Telegram returned ok=false"
                )
                return "unknown", False

            username = data.get("result", {}).get("username", "unknown")
            # 4. EXIT
            log.setup.debug(
                "[channel] _validate_telegram_token: exit — valid",
                extra={"_fields": {"username": username}},
            )
            return username, True

        except Exception as exc:  # noqa: BLE001
            log.setup.warning(
                "[channel] _validate_telegram_token: request failed",
                extra={"_fields": {"reason": str(exc)}},
            )
            return "unknown", False
