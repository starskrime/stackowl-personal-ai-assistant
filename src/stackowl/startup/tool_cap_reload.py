"""Reload handler that hot-applies a new ``tool_count_cap`` to the presented-tool memo.

WHY THIS MODULE EXISTS. ``OrchestratorSettings.tool_count_cap`` is declared
``json_schema_extra={"hot_reload": True}`` and it was not hot-reloadable. The
presented tool array is memoized per session in
:mod:`stackowl.infra.presented_tools`, and the only ``clear()`` callers in ``src/``
were the skill store and two owl paths — none of them a config-reload path. So
changing the cap took effect only for sessions STARTED afterwards.

The operator experience that makes this worth fixing rather than documenting: the
field claims hot-reload, so ``/config set`` returns ``✓ tool_count_cap = 40`` with
NO "restart required" suffix, while every live conversation carries on with the old
roster. The setting was honest about its intent and nothing downstream honoured it.

This is the same defect ``auto_restart.delay_minutes`` had, fixed 2026-08-22, and
this module deliberately mirrors :mod:`stackowl.startup.auto_restart_reload` rather
than inventing a second reload mechanism: same ``settings_reloaded`` event, same
type-guard against the dict payloads the slash commands emit on that event, same
never-raise contract, same named-factory shape so the guard is directly unit
testable.

The DECISION ("did the cap change, and therefore should the memo be dropped?")
deliberately lives with the memo, in
:func:`stackowl.infra.presented_tools.apply_tool_count_cap`, exactly as
``apply_quiet_period`` lives on ``CodeWatcher``. This module is only the wire.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stackowl.config.settings import Settings
from stackowl.infra.observability import log


def make_tool_cap_reload_handler() -> Callable[[Any], None]:
    """Build the ``settings_reloaded`` handler for the presented-tool memo.

    The returned handler:
    - acts ONLY on a ``Settings`` payload (the config/tier slash commands emit
      small dicts on the same event);
    - drops the memoized tool arrays when ``orchestrator.tool_count_cap`` actually
      CHANGED — not on every reload, or an unrelated config edit would wipe every
      live conversation's roster for nothing;
    - never raises, so a reload error can never kill the watcher thread.
    """

    def _on_settings_reloaded(payload: Any) -> None:
        if not isinstance(payload, Settings):
            log.engine.debug(
                "[reload] settings_reloaded: ignoring non-Settings payload",
                extra={"_fields": {"payload_type": type(payload).__name__}},
            )
            return
        try:
            from stackowl.infra import presented_tools

            presented_tools.apply_tool_count_cap(payload.orchestrator.tool_count_cap)
        except Exception as exc:
            log.engine.error(
                "[reload] settings_reloaded: applying tool-count-cap reload failed",
                exc_info=exc,
            )

    return _on_settings_reloaded
