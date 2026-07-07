"""Reload handler that hot-applies a new Settings to the live WebhookReceiver.

Subscribed to the ``settings_reloaded`` event in the gateway lifecycle, same
shape as :mod:`stackowl.startup.provider_reload`. The event is emitted by TWO
producers with DIFFERENT payloads: :class:`stackowl.config.watcher.ConfigWatcher`
emits the new ``Settings`` object; the webhook/provider/config slash commands
(after Plan A) ALSO emit a real ``Settings`` object now — this handler simply
type-guards defensively in case a future producer still emits a dict.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stackowl.config.settings import Settings
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.webhooks.receiver import WebhookReceiver


def make_webhook_reload_handler(receiver: WebhookReceiver) -> Callable[[Any], None]:
    """Build the ``settings_reloaded`` handler bound to ``receiver``."""

    def _on_settings_reloaded(payload: Any) -> None:
        if not isinstance(payload, Settings):
            log.webhook.debug(
                "[webhook] reload: ignoring non-Settings payload",
                extra={"_fields": {"payload_type": type(payload).__name__}},
            )
            return
        try:
            receiver.apply_settings(payload)
        except Exception as exc:
            log.webhook.error(
                "[webhook] reload: applying settings failed",
                exc_info=exc,
            )

    return _on_settings_reloaded
