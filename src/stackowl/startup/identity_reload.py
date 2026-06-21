"""Reload handler that hot-applies new ``identity.aliases`` to the live resolver.

Subscribed to the ``settings_reloaded`` event in the gateway lifecycle. The event
is emitted by TWO producers with DIFFERENT payloads:

- :class:`stackowl.config.watcher.ConfigWatcher` emits the new ``Settings`` object.
- ``config_command`` / ``provider_command`` emit a small ``dict`` (a UI-side
  notification).

So the handler TYPE-GUARDS: it only mutates the resolver for a ``Settings``
payload and ignores everything else. The orchestrator hands BOTH ``StepServices``
and the ``FactExtractor`` the SAME :class:`IdentityResolver` instance, so a single
in-place ``update_aliases`` here propagates to every durable-knowledge consumer
without a restart. Extracted to a named factory (not an inline lambda) so the
type-guard logic is directly unit-testable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stackowl.config.settings import Settings
from stackowl.infra.observability import log
from stackowl.tenancy.identity import IdentityResolver


def make_identity_reload_handler(
    resolver: IdentityResolver,
) -> Callable[[Any], None]:
    """Build the ``settings_reloaded`` handler bound to ``resolver``.

    The returned handler:
    - acts ONLY on a ``Settings`` payload (ignores the dict payloads emitted by
      the config/provider slash commands);
    - never raises — a reload error is logged, so it can never kill the watcher
      thread or the running server.
    """

    def _on_settings_reloaded(payload: Any) -> None:
        if not isinstance(payload, Settings):
            log.tenancy.debug(
                "[identity] settings_reloaded: ignoring non-Settings payload",
                extra={"_fields": {"payload_type": type(payload).__name__}},
            )
            return
        try:
            resolver.update_aliases(payload.identity.aliases)
            log.tenancy.info(
                "[identity] settings_reloaded: alias map refreshed live",
                extra={"_fields": {"identities": len(payload.identity.aliases)}},
            )
        except Exception as exc:
            log.tenancy.error(
                "[identity] settings_reloaded: applying identity reload failed",
                exc_info=exc,
            )

    return _on_settings_reloaded
