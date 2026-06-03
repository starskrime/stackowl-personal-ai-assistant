"""Reload handler that hot-applies a new Settings to the live ProviderRegistry.

Subscribed to the ``settings_reloaded`` event in the gateway lifecycle. The event
is emitted by TWO producers with DIFFERENT payloads:

- :class:`stackowl.config.watcher.ConfigWatcher` emits the new ``Settings`` object.
- ``config_command`` / ``provider_command`` emit a small ``dict`` (e.g.
  ``{"provider": name}``) as a UI-side notification.

So the handler TYPE-GUARDS: it only mutates the registry for a ``Settings``
payload and ignores everything else. Extracted to a named factory (not an inline
lambda) so the type-guard logic is directly unit-testable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stackowl.config.settings import Settings
from stackowl.infra.observability import log
from stackowl.providers.registry import ProviderRegistry


def make_settings_reload_handler(
    provider_registry: ProviderRegistry,
) -> Callable[[Any], None]:
    """Build the ``settings_reloaded`` handler bound to ``provider_registry``.

    The returned handler:
    - acts ONLY on a ``Settings`` payload (ignores the dict payloads emitted by
      the config/provider slash commands);
    - never raises — a reload error is logged, so it can never kill the watcher
      thread or the running server.
    """

    def _on_settings_reloaded(payload: Any) -> None:
        if not isinstance(payload, Settings):
            log.engine.debug(
                "[reload] settings_reloaded: ignoring non-Settings payload",
                extra={"_fields": {"payload_type": type(payload).__name__}},
            )
            return
        try:
            provider_registry.apply_settings(payload)
        except Exception as exc:
            log.engine.error(
                "[reload] settings_reloaded: applying provider reload failed",
                exc_info=exc,
            )

    return _on_settings_reloaded
