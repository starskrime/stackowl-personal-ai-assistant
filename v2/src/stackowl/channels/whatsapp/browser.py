"""WhatsAppBrowserDriver — Playwright-driven WhatsApp Web automation.

This module drives a Chromium browser to interact with WhatsApp Web.
All live I/O paths are guarded by :class:`TestModeGuard`.

IMPORTANT: WhatsApp Web's DOM structure changes frequently. The JS evaluation
selectors used here are best-effort and marked as fragile — the infrastructure
pattern (poll → enqueue → send) is the stable contract, not the specific DOM
queries.

Design note: browser launch and browser actions are kept in one file (< 300
lines). If the file grows beyond 300 lines in future, split into
``browser_launch.py`` and ``browser_actions.py``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from stackowl.channels.whatsapp.session import WhatsAppSessionManager
from stackowl.channels.whatsapp.settings import WhatsAppSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log

if TYPE_CHECKING:
    pass

# Selector for the QR code element present when not authenticated.
_QR_SELECTOR = '[data-testid="qrcode"]'
# Selector that appears after successful authentication.
_AUTH_SELECTOR = '[data-testid="conversation-panel-body"]'

# JS snippet to harvest the latest unread text messages.
# WhatsApp Web's DOM is volatile; this is a best-effort structural probe.
# Returns list[{jid, text, timestamp}] — may return [] on DOM changes.
_POLL_JS = """
() => {
    const results = [];
    try {
        // Each unread conversation row carries data-testid="cell-frame-container".
        // The unread badge selector may change across WhatsApp Web versions.
        const rows = document.querySelectorAll(
            '[data-testid="cell-frame-container"]'
        );
        rows.forEach(row => {
            const badge = row.querySelector('[data-testid="icon-unread-count"]');
            if (!badge) return;
            const jidEl = row.closest('[data-id]');
            const jid = jidEl ? jidEl.getAttribute('data-id') : null;
            if (!jid) return;
            const previewEl = row.querySelector('[data-testid="last-msg-status"]')
                || row.querySelector('span[title]');
            const text = previewEl ? (previewEl.getAttribute('title') || previewEl.innerText || '') : '';
            results.push({ jid, text, timestamp: Date.now() });
        });
    } catch (_) {}
    return results;
}
"""


class WhatsAppBrowserDriver:
    """Controls a Playwright Chromium browser session on WhatsApp Web.

    Lifecycle:
        1. ``start()``  — launch browser, optionally restore session, handle QR.
        2. ``poll_messages()`` — lightweight JS eval to find unread messages.
        3. ``send_message(jid, text)`` — navigate to chat and type the message.
        4. ``stop()`` — save session state and close browser.
    """

    def __init__(
        self,
        settings: WhatsAppSettings,
        session_manager: WhatsAppSessionManager,
    ) -> None:
        self._settings = settings
        self._session_manager = session_manager
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        log.whatsapp.debug(
            "[whatsapp] browser_driver.init: ready",
            extra={"_fields": {"headless": settings.headless}},
        )

    async def start(self) -> None:
        """Launch Chromium, restore session if available, handle QR if needed.

        4-point logging: entry / decision / step / exit.
        """
        log.whatsapp.debug("[whatsapp] browser_driver.start: entry")
        TestModeGuard.assert_not_test_mode("whatsapp.browser.start")

        # Deferred import: playwright is optional — only needed at runtime.
        from playwright.async_api import async_playwright

        storage_state = self._session_manager.load()
        log.whatsapp.debug(
            "[whatsapp] browser_driver.start: decision session_state",
            extra={"_fields": {"has_session": storage_state is not None}},
        )

        pw = await async_playwright().__aenter__()
        launch_kwargs: dict[str, Any] = {"headless": self._settings.headless}
        self._browser = await pw.chromium.launch(**launch_kwargs)

        ctx_kwargs: dict[str, Any] = {}
        if storage_state is not None:
            ctx_kwargs["storage_state"] = storage_state

        self._context = await self._browser.new_context(**ctx_kwargs)
        self._page = await self._context.new_page()

        log.whatsapp.debug("[whatsapp] browser_driver.start: step navigating_to_whatsapp_web")
        await self._page.goto(
            "https://web.whatsapp.com",
            timeout=self._settings.page_load_timeout_ms,
        )

        # Check if QR code is visible (not authenticated yet).
        qr_visible = await self._page.is_visible(_QR_SELECTOR)
        log.whatsapp.debug(
            "[whatsapp] browser_driver.start: decision qr_visible",
            extra={"_fields": {"qr_visible": qr_visible}},
        )
        if qr_visible:
            log.whatsapp.warning(
                "[whatsapp] browser_driver.start: QR code scan required — open WhatsApp Web in your phone and scan the QR code displayed in the browser window"
            )
            # Wait for the user to scan the QR — poll until conversation panel appears.
            await self._page.wait_for_selector(
                _AUTH_SELECTOR,
                timeout=120_000,  # 2-minute window to scan
            )
            log.whatsapp.debug("[whatsapp] browser_driver.start: step qr_scan_complete")

        # Save session state after successful authentication.
        await self._save_session()
        log.whatsapp.debug("[whatsapp] browser_driver.start: exit")

    async def poll_messages(self) -> list[dict[str, Any]]:
        """Scan for unread messages via lightweight JS evaluation.

        4-point logging: entry / decision / step / exit.

        Returns:
            List of dicts with keys ``jid``, ``text``, ``timestamp``.
            Returns empty list if page is unavailable or DOM changed.
        """
        log.whatsapp.debug("[whatsapp] browser_driver.poll_messages: entry")
        if self._page is None:
            log.whatsapp.warning("[whatsapp] browser_driver.poll_messages: no page — returning empty")
            return []

        log.whatsapp.debug("[whatsapp] browser_driver.poll_messages: decision js_eval")
        try:
            raw: list[dict[str, Any]] = await self._page.evaluate(_POLL_JS)
            log.whatsapp.debug(
                "[whatsapp] browser_driver.poll_messages: step js_evaluated",
                extra={"_fields": {"message_count": len(raw)}},
            )
        except Exception as exc:
            log.whatsapp.error(
                "[whatsapp] browser_driver.poll_messages: js_eval failed",
                exc_info=exc,
            )
            return []

        log.whatsapp.debug(
            "[whatsapp] browser_driver.poll_messages: exit",
            extra={"_fields": {"message_count": len(raw)}},
        )
        return raw

    async def send_message(self, jid: str, text: str) -> None:
        """Navigate to a WhatsApp chat and send a text message.

        4-point logging: entry / decision / step / exit.

        Args:
            jid: WhatsApp JID (``phone@s.whatsapp.net``).
            text: Text content to send.
        """
        log.whatsapp.debug(
            "[whatsapp] browser_driver.send_message: entry",
            extra={"_fields": {"text_len": len(text)}},
        )
        TestModeGuard.assert_not_test_mode("whatsapp.browser.send")
        if self._page is None:
            log.whatsapp.error("[whatsapp] browser_driver.send_message: no page available")
            return

        phone = jid.split("@")[0] if "@" in jid else jid
        chat_url = f"https://web.whatsapp.com/send?phone={phone}"
        log.whatsapp.debug(
            "[whatsapp] browser_driver.send_message: decision navigate_to_chat",
            extra={"_fields": {"chat_url_path": f"/send?phone=<redacted>"}},
        )

        await self._page.goto(chat_url, timeout=self._settings.page_load_timeout_ms)
        # Wait for the message input box to be available.
        input_selector = '[data-testid="conversation-compose-box-input"]'
        await self._page.wait_for_selector(input_selector, timeout=15_000)

        log.whatsapp.debug("[whatsapp] browser_driver.send_message: step typing_message")
        await self._page.fill(input_selector, text)
        await self._page.keyboard.press("Enter")

        log.whatsapp.debug(
            "[whatsapp] browser_driver.send_message: exit",
            extra={"_fields": {"text_len": len(text)}},
        )

    async def stop(self) -> None:
        """Save session state and close the browser.

        4-point logging: entry / decision / step / exit.
        """
        log.whatsapp.debug("[whatsapp] browser_driver.stop: entry")
        if self._page is not None:
            try:
                await self._save_session()
                log.whatsapp.debug("[whatsapp] browser_driver.stop: step session_saved")
            except Exception as exc:
                log.whatsapp.error(
                    "[whatsapp] browser_driver.stop: session save failed",
                    exc_info=exc,
                )

        if self._browser is not None:
            log.whatsapp.debug("[whatsapp] browser_driver.stop: decision closing_browser")
            try:
                await self._browser.close()
            except Exception as exc:
                log.whatsapp.error(
                    "[whatsapp] browser_driver.stop: browser close failed",
                    exc_info=exc,
                )
        log.whatsapp.debug("[whatsapp] browser_driver.stop: exit")

    async def _save_session(self) -> None:
        """Persist the current browser storage state to disk."""
        if self._context is None:
            return
        try:
            state = await self._context.storage_state()
            self._session_manager.save(state)
        except Exception as exc:
            log.whatsapp.error(
                "[whatsapp] browser_driver._save_session: failed",
                exc_info=exc,
            )
