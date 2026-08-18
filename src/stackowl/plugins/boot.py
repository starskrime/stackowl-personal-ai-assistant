"""Load installed plugins at startup — Bakir's decision, 2026-08-17.

He chose "plugins on load" over load-on-explicit-command and over leaving the
loader unwired. I had recommended the explicit command, on the grounds that
installing a plugin should stay a deliberate act; his call stands, and this is
built to it rather than around it.

WHAT WAS ACTUALLY BROKEN. Boot constructed ``PluginIndex`` (a catalogue) and
``PluginRegistry`` (a table), and nothing anywhere in ``src/`` ever constructed
``LocalPluginLoader`` — the only thing that imports a plugin module and registers
its classes. A plugin dropped into ``~/.stackowl/plugins/`` was COUNTED at boot and
loaded nothing. The 2026-08-16 install-path proof drove the loader by hand, which
proved the loader works, not that anything calls it.

WHAT AUTOMATIC LOADING OBLIGES, and why each guard below exists. The platform now
executes third-party code during startup:

* A plugin that RAISES is skipped, not fatal. StackOwl is the user's entire
  assistant; losing it to one bad plugin is a far worse outcome than losing the
  plugin.
* A plugin that HANGS cannot wedge startup. A bare ``await`` on third-party code
  makes the boot hostage to it, so each load is time-bounded and abandoned on
  expiry.
* The capability gate stays where it already is — LOAD time, inside the loader — so
  an ungranted plugin never reaches a call site.
* One INFO line reports what loaded, what was skipped and why. Nobody is watching a
  boot, so a plugin that silently failed to load would look exactly like a plugin
  that was never installed.

The loader itself is SYNCHRONOUS (``LocalPluginLoader.load`` imports a module),
which is why each load runs in a thread rather than being awaited directly: a
synchronous import that blocks would otherwise stall the whole event loop, not just
this coroutine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stackowl.infra.observability import log

#: How long one plugin gets to import and register before the boot moves on.
#: Generous for a real plugin (an import plus a handful of registrations) and short
#: enough that a hanging one costs seconds, not a startup.
DEFAULT_PLUGIN_LOAD_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class PluginBootReport:
    """What happened, in a form the caller can log and a test can assert."""

    loaded: tuple[str, ...] = ()
    #: (plugin name, why it was skipped) — kept as a pair so the boot line can say
    #: WHICH plugin failed and WHY, rather than only that something did.
    skipped: tuple[tuple[str, str], ...] = ()
    _errors: list[str] = field(default_factory=list, repr=False)


async def load_installed_plugins(
    *,
    index: Any,
    loader: Any,
    timeout_seconds: float = DEFAULT_PLUGIN_LOAD_TIMEOUT_SECONDS,
) -> PluginBootReport:
    """Import and register every installed plugin. NEVER raises.

    Returns a report rather than logging and forgetting, so the caller owns the
    boot line and a test can assert the outcome without parsing logs.
    """
    if index is None or loader is None:
        log.startup.debug(
            "[plugins] boot: not wired — no plugins will load",
            extra={"_fields": {"has_index": index is not None,
                               "has_loader": loader is not None}},
        )
        return PluginBootReport()

    try:
        entries = list(index.all())
    except Exception as exc:
        # An unreadable index must not cost the boot. The platform starts without
        # plugins, and says so.
        log.startup.error(
            "[plugins] boot: could not read the plugin index — starting with NO "
            "plugins loaded",
            exc_info=exc,
        )
        return PluginBootReport()

    if not entries:
        log.startup.info("[plugins] boot: no plugins installed")
        return PluginBootReport()

    loaded: list[str] = []
    skipped: list[tuple[str, str]] = []
    for entry in entries:
        name = str(getattr(entry, "name", "") or "?")
        raw_path = getattr(entry, "path", None)
        if not raw_path:
            skipped.append((name, "no install path recorded"))
            continue
        try:
            # to_thread: LocalPluginLoader.load is synchronous and IMPORTS a
            # module. Awaiting it directly would run third-party import code on
            # the event loop, so one blocking plugin would stall everything, not
            # just this load.
            await asyncio.wait_for(
                asyncio.to_thread(loader.load, Path(str(raw_path))),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            skipped.append((name, f"timed out after {timeout_seconds:g}s"))
            log.startup.error(
                "[plugins] boot: plugin load TIMED OUT — skipped so the boot can "
                "continue",
                extra={"_fields": {"plugin": name, "timeout_s": timeout_seconds}},
            )
        except Exception as exc:
            skipped.append((name, f"{type(exc).__name__}: {exc}"[:200]))
            log.startup.error(
                "[plugins] boot: plugin failed to load — skipped so the boot can "
                "continue",
                exc_info=exc, extra={"_fields": {"plugin": name}},
            )
        else:
            loaded.append(name)

    # ONE line, at INFO, naming both halves. A plugin that silently failed to load
    # looks exactly like a plugin that was never installed, and nobody is watching
    # a boot.
    log.startup.info(
        "[plugins] boot: exit",
        extra={"_fields": {"loaded": loaded, "skipped": [n for n, _ in skipped],
                           "installed": len(entries)}},
    )
    return PluginBootReport(loaded=tuple(loaded), skipped=tuple(skipped))
