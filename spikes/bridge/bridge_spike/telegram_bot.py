"""The kit's own throwaway *test* Telegram bot (AD-16, AD-37): delivers the
setup code and device-approval prompts over a separate bot token that must
never collide with the platform's live `telegram_channel.bot_token`.
Telegram allows only one poller per bot token -- a second one starting
against the platform's real bot would knock it offline, which is why this
module refuses to start rather than merely warning.

This kit's own test bot token/allowed-user-ids come from ITS OWN env vars
(`TEST_BOT_TOKEN_ENV`/`TEST_ALLOWED_USER_IDS_ENV` below), never from the
platform's config. Real Telegram Bot API traffic is never exercised by this
module's own test suite -- tests inject a fake `Application`/`Bot`.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import yaml
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, ContextTypes

logger = logging.getLogger("bridge_spike.telegram_bot")

TEST_BOT_TOKEN_ENV = "BRIDGE_SPIKE_TEST_TELEGRAM_BOT_TOKEN"
TEST_ALLOWED_USER_IDS_ENV = "BRIDGE_SPIKE_TEST_TELEGRAM_USER_IDS"

ApproveCallback = Callable[[str, str], Awaitable[None]]  # (request_id, code) -> None, raises on mismatch


class BotTokenCollisionError(RuntimeError):
    """Raised when the kit's own test bot token equals the platform's."""


def _platform_config_path() -> Path:
    """Mirrors `stackowl.paths.StackowlHome.config_file`'s exact resolution
    order without importing the platform package: `STACKOWL_CONFIG_FILE` env
    var if set, else `~/.stackowl/stackowl.yaml`."""
    raw = os.environ.get("STACKOWL_CONFIG_FILE")
    return Path(raw) if raw else Path.home() / ".stackowl" / "stackowl.yaml"


def _resolve_secret_ref(raw: str) -> str | None:
    """Re-implements the platform's `keychain:`/`file:`/bare-env-var secret
    reference prefixes (see `stackowl.config.secret_resolver.SecretResolver`)
    without importing the platform package -- same spirit as this kit's own
    CA/mDNS code re-implementing rather than importing.

    Returns None (never raises) on any resolution failure: this is a
    best-effort SAFETY check, not a config loader, and a platform config
    problem unrelated to this kit must not block the kit from starting.
    """
    try:
        if raw.startswith("keychain:"):
            try:
                import keyring  # local import: optional dependency, same as the platform's resolver

                service = raw[len("keychain:") :]
                return keyring.get_password(service, service)
            except Exception as exc:  # noqa: BLE001 — a keyring failure must not block this check
                logger.warning("telegram_bot: could not resolve keychain: secret for collision check: %s", exc)
                return None
        if raw.startswith("file:"):
            path = Path(raw[len("file:") :])
            return path.read_text(encoding="utf-8").strip()
        # Bare value: the platform treats this as an environment variable
        # NAME to look up (SecretResolver._from_env), not a literal secret.
        return os.environ.get(raw)
    except OSError as exc:
        logger.warning("telegram_bot: could not resolve %r for collision check: %s", raw, exc)
        return None


def resolve_platform_telegram_bot_token(config_path: Path | None = None) -> str | None:
    """The platform's own resolved `telegram_channel.bot_token`, or None if
    there is no platform config, no such key, or it could not be resolved."""
    path = config_path or _platform_config_path()
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("telegram_bot: could not read/parse platform config %s for collision check: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    telegram_channel = data.get("telegram_channel")
    if not isinstance(telegram_channel, dict):
        return None
    raw_token = str(telegram_channel.get("bot_token") or "").strip()
    if not raw_token:
        return None
    return _resolve_secret_ref(raw_token)


def check_token_collision(kit_token: str, config_path: Path | None = None) -> str | None:
    """A printable remedy string if `kit_token` equals the platform's
    resolved `telegram_channel.bot_token`, else None. Resolves the platform
    token fresh on every call -- this kit never caches or logs either raw
    token value."""
    platform_token = resolve_platform_telegram_bot_token(config_path)
    if platform_token and hmac.compare_digest(kit_token, platform_token):
        return (
            "Refusing to start the kit's test Telegram bot: its token is identical to the "
            "platform's configured telegram_channel.bot_token. Telegram allows only one poller "
            "per bot token, and starting this one would knock the live platform bot offline.\n"
            f"Remedy: create a SEPARATE bot via @BotFather for kit testing and set "
            f"{TEST_BOT_TOKEN_ENV} to its token instead."
        )
    return None


def allowed_user_ids_from_env(raw: str | None) -> list[int]:
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            logger.warning("telegram_bot: ignoring non-integer entry in %s: %r", TEST_ALLOWED_USER_IDS_ENV, part)
    return ids


class TestTelegramBot:
    """Thin wrapper around `python-telegram-bot`'s `Application` for the
    kit's own throwaway test bot."""

    def __init__(
        self,
        token: str,
        allowed_user_ids: list[int],
        *,
        config_path: Path | None = None,
        approve_callback: ApproveCallback | None = None,
        application: Application | None = None,
    ) -> None:
        collision = check_token_collision(token, config_path)
        if collision is not None:
            raise BotTokenCollisionError(collision)
        self._allowed_user_ids = list(allowed_user_ids)
        self._approve_callback = approve_callback
        self._application = application or ApplicationBuilder().token(token).build()
        if approve_callback is not None:
            self._application.add_handler(CallbackQueryHandler(self._on_callback_query))
        self._initialized = False
        self._started = False

    @property
    def single_allowed_user_id(self) -> int | None:
        """The one allowed test user, or None when zero or more than one are
        configured -- the AC's "terminal-only for none or several" case."""
        return self._allowed_user_ids[0] if len(self._allowed_user_ids) == 1 else None

    async def start(self) -> None:
        await self._application.initialize()
        self._initialized = True
        await self._application.start()
        if self._application.updater is not None:
            await self._application.updater.start_polling()
        self._started = True

    async def stop(self) -> None:
        """Tears down whatever `start()` got through, not only a fully
        successful run: `shutdown()` must still run if `initialize()`
        allocated anything (a bot HTTP client, persistence, ...) even when a
        LATER step in `start()` (e.g. `updater.start_polling()`) failed."""
        if not self._initialized:
            return
        if self._started:
            if self._application.updater is not None:
                await self._application.updater.stop()
            await self._application.stop()
            self._started = False
        await self._application.shutdown()
        self._initialized = False

    async def send_setup_code(self, code: str) -> bool:
        """Sends the setup code to the single allowed test user. Returns
        False (nothing sent -- terminal-only) when zero or several users are
        configured, per the AC."""
        user_id = self.single_allowed_user_id
        if user_id is None:
            return False
        await self._application.bot.send_message(chat_id=user_id, text=f"Bridge setup code: {code}")
        return True

    async def send_device_request(self, device_name: str, code: str, request_id: str) -> bool:
        """Sends the pending device-approval prompt with an inline Approve
        button. The callback data carries the request id AND the code
        (never the device name -- Telegram caps `callback_data` at 64 bytes,
        and a name can be up to 64 characters on its own) so approval can
        check both, the same as the first-device signed-tap path. Same
        "single allowed user" gate as `send_setup_code`."""
        user_id = self.single_allowed_user_id
        if user_id is None:
            return False
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Approve", callback_data=f"approve:{request_id}:{code}")]]
        )
        await self._application.bot.send_message(
            chat_id=user_id,
            text=f"New device requesting access: {device_name}\nMatching code: {code}",
            reply_markup=keyboard,
        )
        return True

    async def _on_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        query = update.callback_query
        if query is None or not isinstance(query.data, str) or not query.data.startswith("approve:"):
            return
        remainder = query.data[len("approve:") :]
        request_id, _, code = remainder.partition(":")
        if self._approve_callback is not None:
            try:
                await self._approve_callback(request_id, code)
            except Exception as exc:  # noqa: BLE001 — a bad tap must not crash the bot's update loop
                logger.warning("telegram_bot: approve callback failed for request %s: %s", request_id, exc)
        await query.answer()
