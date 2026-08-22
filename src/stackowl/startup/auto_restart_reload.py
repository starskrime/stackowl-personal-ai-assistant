"""Reload handler that hot-applies a new Settings to the live CodeWatcher.

BAKIR, 2026-08-22: "Why core take long time to restart himself to use the new
code". Measured end to end: ~5.5 minutes, of which 300 SECONDS is a quiet-period
debounce and ~25s is the actual boot (migrations are 7ms). The wait is the whole
cost, and `auto_restart.delay_minutes` is the knob for it.

WHY THIS MODULE EXISTS. That field is declared ``json_schema_extra={"hot_reload":
True}`` and it was not hot-reloadable. ``CodeWatcher`` is constructed ONCE in the
startup path with ``quiet_period_s=auto.delay_minutes * 60.0`` and nothing ever
re-read it — so lowering the delay did nothing until the next restart, and that
restart still used the OLD window. The operator shortens the wait and then waits
the old amount to find out whether it worked.

That is the first of this codebase's four recurring shapes — a write with no
reader — wearing a config marker instead of a database column. The setting was
honest about its intent and nothing downstream honoured it.

Mirrors ``provider_reload`` and ``webhook_reload`` exactly: same
``settings_reloaded`` event, same type-guard against the dict payloads the slash
commands emit, same never-raise contract, same named-factory shape so the guard is
directly unit-testable. A second reload mechanism would be the duplication
CLAUDE.md forbids.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stackowl.config.settings import Settings
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.runtime.code_watcher import CodeWatcher


def make_auto_restart_reload_handler(
    code_watcher: CodeWatcher,
) -> Callable[[Any], None]:
    """Build the ``settings_reloaded`` handler bound to ``code_watcher``.

    The returned handler:
    - acts ONLY on a ``Settings`` payload (the config/provider slash commands emit
      small dicts on the same event);
    - applies ``runtime.auto_restart.delay_minutes`` to the live watcher;
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
            auto = payload.runtime.auto_restart
            code_watcher.apply_quiet_period(auto.delay_minutes * 60.0)
        except Exception as exc:
            log.engine.error(
                "[reload] settings_reloaded: applying auto-restart reload failed",
                exc_info=exc,
            )

    return _on_settings_reloaded
