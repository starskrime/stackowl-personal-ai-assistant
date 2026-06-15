"""Discord button-interaction routing + a View factory for inline keyboards.

Discord's rich interactive surface is ``discord.ui.View`` + ``discord.ui.Button``.
A button tap fires an ``Interaction`` carrying the button's ``custom_id`` — the
Discord analogue of Telegram's ``callback_data`` / Slack's ``action_id``. This
module mirrors the Telegram :class:`~stackowl.channels.telegram.callbacks.CallbackRouter`
prefix-dispatch shape (consent / clarify / memory all register a prefix) so the
three F005 handlers are wired identically across channels.

:class:`DiscordCallbackRouter` dispatches a tapped ``custom_id`` to the
longest-matching registered prefix handler ``(callback_id, custom_id)``. It is
in-memory (no SQLite idempotency table): discord.py already de-dups interactions
and acknowledges them via the interaction response, so a persistent log would be
redundant — the handlers themselves are idempotent (a stale consent rid / clarify
id is a logged no-op).

:func:`build_view` turns a Telegram-style keyboard dict
(``{"inline_keyboard": [[{"text", "callback_data"}, …], …]}``) into a live
``discord.ui.View`` whose every button, on tap, acks the interaction and routes
its ``custom_id`` through the adapter's attached router. Built lazily (discord
is imported here, not at module import of the adapter's hot path) and fail-safe:
a malformed row is skipped, never crashes the send.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.channels.discord.adapter import DiscordChannelAdapter

__all__ = ["DiscordCallbackRouter", "build_view"]

_Handler = Callable[[str, str], Awaitable[None]]


class DiscordCallbackRouter:
    """Routes a tapped button ``custom_id`` to a registered prefix handler.

    Mirrors the Telegram ``CallbackRouter`` register/route contract (prefix →
    ``(callback_id, custom_id)`` handler, longest-prefix wins) so consent,
    clarify, and memory wire identically across channels.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, _Handler] = {}
        log.discord.debug("[discord] callbacks.router.init: entry")

    def register(self, prefix: str, handler: _Handler) -> None:
        """Register ``handler`` for ``custom_id`` values beginning with ``prefix``."""
        log.discord.debug(
            "[discord] callbacks.router.register: entry",
            extra={"_fields": {"prefix": prefix}},
        )
        self._handlers[prefix] = handler
        log.discord.debug(
            "[discord] callbacks.router.register: exit",
            extra={"_fields": {"registered_count": len(self._handlers)}},
        )

    async def route(self, callback_id: str, custom_id: str) -> None:
        """Dispatch ``custom_id`` to the longest-matching prefix handler.

        4-point logging: entry / decision / step / exit. Never raises — a handler
        error is logged so the interaction loop survives.
        """
        log.discord.debug(
            "[discord] callbacks.router.route: entry",
            extra={"_fields": {"data_prefix": custom_id[:16]}},
        )
        handler: _Handler | None = None
        matched_prefix = ""
        for prefix, h in self._handlers.items():
            if custom_id.startswith(prefix) and len(prefix) >= len(matched_prefix):
                handler = h
                matched_prefix = prefix
        log.discord.debug(
            "[discord] callbacks.router.route: decision handler_lookup",
            extra={"_fields": {"matched_prefix": matched_prefix or "none"}},
        )
        if handler is None:
            log.discord.warning(
                "[discord] callbacks.router.route: no handler for prefix",
                extra={"_fields": {"data_prefix_8": custom_id[:8]}},
            )
            return
        try:
            await handler(callback_id, custom_id)
        except Exception as exc:  # never crash the interaction loop
            log.discord.error(
                "[discord] callbacks.router.route: handler raised",
                exc_info=exc,
                extra={"_fields": {"matched_prefix": matched_prefix}},
            )
        log.discord.debug("[discord] callbacks.router.route: exit")


def build_view(
    keyboard: dict[str, object],
    adapter: DiscordChannelAdapter,
) -> Any:
    """Build a ``discord.ui.View`` from a Telegram-style inline-keyboard dict.

    Each ``{"text", "callback_data"}`` button becomes a ``discord.ui.Button``
    whose ``custom_id`` is the original ``callback_data`` (so the same
    ``consent:{rid}:{scope}`` / ``clarify:{id}:{idx}`` / ``mem:…`` payloads route
    identically to Telegram/Slack). On tap the button acks the interaction and
    routes ``custom_id`` through the adapter's attached callback router.

    Fail-safe: a malformed/empty row is skipped; an unattached router makes the
    tap a logged no-op (the prompt still fails closed on timeout). discord is
    imported lazily so the adapter's hot path never depends on it.
    """
    import discord

    view = discord.ui.View(timeout=None)
    rows = keyboard.get("inline_keyboard", []) if isinstance(keyboard, dict) else []
    n = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, list):
            continue
        for btn in row:
            if not isinstance(btn, dict):
                continue
            label = str(btn.get("text", "") or "")
            custom_id = str(btn.get("callback_data", "") or "")
            if not custom_id:
                continue
            button: Any = discord.ui.Button(label=label, custom_id=custom_id)

            async def _on_click(
                interaction: Any, _cid: str = custom_id
            ) -> None:
                # Ack FIRST (discord requires a response within 3s) so a slow/
                # raising route never wedges the interaction; then route.
                callback_id = str(getattr(interaction, "id", "") or "")
                try:
                    await interaction.response.defer()
                except Exception as exc:  # ack is best-effort
                    log.discord.error(
                        "[discord] callbacks.view: interaction defer failed",
                        exc_info=exc,
                    )
                router = adapter.callback_router
                if router is None:
                    log.discord.warning(
                        "[discord] callbacks.view: no router attached — tap ignored",
                    )
                    return
                await router.route(callback_id, _cid)

            button.callback = _on_click
            view.add_item(button)
            n += 1
    log.discord.debug(
        "[discord] callbacks.build_view: built",
        extra={"_fields": {"button_count": n}},
    )
    return view
