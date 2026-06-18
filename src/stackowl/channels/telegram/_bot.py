"""Internal bot lifecycle and keyboard-building helpers for TelegramChannelAdapter.

Extracted to keep adapter.py ≤ 300 lines (constraint B2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from telegram import InlineKeyboardMarkup
    from telegram.ext import Application


async def start_bot(
    bot_token: str,
    webhook_url: str | None,
    webhook_secret: str,
) -> tuple[Any, int, str]:
    """Build, initialize, and start a PTB Application.

    Returns:
        ``(app, bot_user_id, bot_username)`` triple.
    """
    from telegram.ext import Application, ApplicationBuilder, MessageHandler, filters  # noqa: F811

    log.telegram.debug("[telegram] _bot.start_bot: entry")
    app: Application = ApplicationBuilder().token(bot_token).build()  # type: ignore[type-arg]
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
            await app.updater.start_polling(drop_pending_updates=True)

    log.telegram.debug("[telegram] _bot.start_bot: exit")
    return app, bot_user_id, bot_username


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
