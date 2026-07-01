"""Internal bot lifecycle and keyboard-building helpers for TelegramChannelAdapter.

Extracted to keep adapter.py ≤ 300 lines (constraint B2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from telegram.ext import Application

# PB1: bounded timeouts so a stalled socket fails LOUD (raises TimedOut) and PTB
# reconnects, instead of hanging the long-poll loop indefinitely (RC0 fix).
# get_updates_read_timeout MUST be strictly greater than the long-poll `timeout`
# kwarg or PTB raises BadRequest; +10s headroom for clock skew + handshake.
TELEGRAM_CONNECT_TIMEOUT = 10.0
TELEGRAM_READ_TIMEOUT = 20.0
TELEGRAM_WRITE_TIMEOUT = 20.0
TELEGRAM_POOL_TIMEOUT = 10.0
TELEGRAM_LONG_POLL_TIMEOUT = 30  # seconds the bot asks Telegram to hold the long-poll open
TELEGRAM_GET_UPDATES_READ_TIMEOUT = float(TELEGRAM_LONG_POLL_TIMEOUT + 10)


async def start_bot(
    bot_token: str,
    webhook_url: str | None,
    webhook_secret: str,
) -> tuple[Any, int, str]:
    """Build, initialize, and start a PTB Application.

    Returns:
        ``(app, bot_user_id, bot_username)`` triple.
    """
    from telegram.ext import ApplicationBuilder

    log.telegram.debug(
        "[telegram] _bot.start_bot: entry",
        extra={
            "_fields": {
                "connect_timeout": TELEGRAM_CONNECT_TIMEOUT,
                "read_timeout": TELEGRAM_READ_TIMEOUT,
                "write_timeout": TELEGRAM_WRITE_TIMEOUT,
                "pool_timeout": TELEGRAM_POOL_TIMEOUT,
                "long_poll_timeout": TELEGRAM_LONG_POLL_TIMEOUT,
                "get_updates_read_timeout": TELEGRAM_GET_UPDATES_READ_TIMEOUT,
            }
        },
    )
    app: Application = (  # type: ignore[type-arg]
        ApplicationBuilder()
        .token(bot_token)
        .connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
        .read_timeout(TELEGRAM_READ_TIMEOUT)
        .write_timeout(TELEGRAM_WRITE_TIMEOUT)
        .pool_timeout(TELEGRAM_POOL_TIMEOUT)
        .get_updates_connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
        .get_updates_read_timeout(TELEGRAM_GET_UPDATES_READ_TIMEOUT)
        .get_updates_write_timeout(TELEGRAM_WRITE_TIMEOUT)
        .get_updates_pool_timeout(TELEGRAM_POOL_TIMEOUT)
        .build()
    )
    await app.initialize()

    bot_info = await app.bot.get_me()
    bot_user_id: int = bot_info.id
    bot_username: str = bot_info.username or ""
    log.telegram.debug(
        "[telegram] _bot.start_bot: step bot_identity_resolved",
        extra={
            "_fields": {
                "bot_id": bot_user_id,
                "bot_username_len": len(bot_username),
            }
        },
    )

    if webhook_url:
        log.telegram.debug(
            "[telegram] _bot.start_bot: decision webhook_mode",
            extra={"_fields": {"webhook_url_len": len(webhook_url)}},
        )
        await app.bot.set_webhook(
            url=webhook_url,
            secret_token=webhook_secret or None,
        )
        await app.start()
    else:
        log.telegram.debug("[telegram] _bot.start_bot: decision polling_mode")
        await app.start()
        if app.updater:
            await app.updater.start_polling(
                drop_pending_updates=True,
                timeout=TELEGRAM_LONG_POLL_TIMEOUT,
            )

    log.telegram.debug("[telegram] _bot.start_bot: exit")
    return app, bot_user_id, bot_username


async def restart_polling(app: Any) -> None:
    """Restart ONLY the long-poll updater on an already-initialized Application,
    WITHOUT rebuilding or shutting it down (PB0c/RC0 gateway-local self-heal).

    Verified against PTB 22.7: ``Updater.stop()`` sets ``running=False`` but leaves
    the updater ``_initialized``, so ``start_polling()`` can be called again on the
    SAME app (it only requires ``_initialized`` True + ``running`` False). We
    deliberately do NOT reuse ``stop_bot`` here — that also ``app.stop()`` +
    ``app.shutdown()``s, which de-initializes the updater and would force a full
    rebuild. No-op (warn) in webhook mode / before start() — there is no updater.
    """
    log.telegram.debug("[telegram] _bot.restart_polling: entry")
    updater = getattr(app, "updater", None) if app is not None else None
    if updater is None:
        log.telegram.warning(
            "[telegram] _bot.restart_polling: no updater — no-op (webhook mode / not started)"
        )
        return
    # stop() raises if the updater is not running; only stop a still-running one.
    if getattr(updater, "running", False):
        log.telegram.debug(
            "[telegram] _bot.restart_polling: decision updater still running — stopping first"
        )
        await updater.stop()
    log.telegram.debug("[telegram] _bot.restart_polling: step re-starting long-poll")
    await updater.start_polling(
        drop_pending_updates=True,
        timeout=TELEGRAM_LONG_POLL_TIMEOUT,
    )
    log.telegram.debug("[telegram] _bot.restart_polling: exit — polling restarted")


async def stop_bot(app: Any) -> None:
    """Gracefully stop and shut down a PTB Application."""
    log.telegram.debug("[telegram] _bot.stop_bot: entry")
    if app is not None:
        if app.updater:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
    log.telegram.debug("[telegram] _bot.stop_bot: exit")


def build_inline_keyboard(keyboard: dict[str, object]) -> Any:
    """Convert a StackOwl keyboard dict to a Telegram InlineKeyboardMarkup.

    Args:
        keyboard: Dict with ``inline_keyboard`` list-of-lists of button dicts.

    Returns:
        An ``InlineKeyboardMarkup`` instance or ``None`` if the dict is malformed.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    log.telegram.debug("[telegram] _bot.build_inline_keyboard: entry")
    raw_rows = keyboard.get("inline_keyboard")
    if not isinstance(raw_rows, list):
        log.telegram.debug("[telegram] _bot.build_inline_keyboard: exit — no rows")
        return None

    button_rows: list[list[InlineKeyboardButton]] = []
    for row in raw_rows:
        if not isinstance(row, list):
            continue
        btn_row: list[InlineKeyboardButton] = []
        for btn in row:
            if not isinstance(btn, dict):
                continue
            btn_row.append(
                InlineKeyboardButton(
                    text=str(btn.get("text", "")),
                    callback_data=str(btn.get("callback_data", "")),
                )
            )
        if btn_row:
            button_rows.append(btn_row)

    if not button_rows:
        log.telegram.debug("[telegram] _bot.build_inline_keyboard: exit — empty rows")
        return None

    markup = InlineKeyboardMarkup(button_rows)
    log.telegram.debug(
        "[telegram] _bot.build_inline_keyboard: exit",
        extra={"_fields": {"row_count": len(button_rows)}},
    )
    return markup
