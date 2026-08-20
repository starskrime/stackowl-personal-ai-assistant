"""Load installed plugins at startup — Bakir's decision, 2026-08-17.

He chose "plugins on load" over load-on-explicit-command and over leaving the
loader unwired. I had recommended the explicit command, on the grounds that
installing a plugin should stay a deliberate act; his call stands, and this is
built to it rather than around it.

WHAT WAS ACTUALLY BROKEN. Boot constructed ``PluginIndex`` (a catalogue) and
``PluginRegistry`` (a table), and nothing anywhere in ``src/`` ever constructed
``LocalPluginLoader`` — the only thing that imports a plugin module and registers
its classes. A plugin dropped into ``~/.stackowl/plugins/`` was COUNTED at boot and
loaded nothing.

AND THEN IT WAS BROKEN AGAIN, ONE LAYER DOWN. The first fix iterated
``PluginIndex``, which is not a list of installed plugins at all: it is the
DOWNLOADABLE CATALOGUE read from ``plugin-index.yaml``, and ``PluginIndexEntry``
carries ``name``/``url``/``version``/``sha256`` and NO path. So every entry was
skipped as "no install path recorded", an absent catalogue file read as "no plugins
installed", and a real plugin sitting in the directory was still never loaded. It
survived a test suite because the test's own index double invented a ``path``
attribute the real class does not have — a double that had stopped resembling the
thing it stands for. FOUND 2026-08-19 by installing an actual plugin and watching
it not load.

THE DIRECTORY IS THE SOURCE, and that is Bakir's ESC-16 decision rather than a
convenience: pip entry points were refused so that installing a plugin stays an
explicit act by the operator — the directory IS the consent. So this walks
``~/.stackowl/plugins/*/plugin.yaml``, the exact layout ``_install_local_plugin``
writes.

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
* A plugin the operator DISABLED stays disabled. ``/plugins disable`` writes
  ``enabled = 0``, and a boot that loaded it anyway would be an actuator the
  operator can see and the platform ignores.
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
from dataclasses import dataclass
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


def _installed_plugin_dirs(plugins_dir: Path) -> list[Path]:
    """Every directory under ``plugins_dir`` holding a ``plugin.yaml``.

    One level deep, because that is the layout the installer writes
    (``~/.stackowl/plugins/<name>/plugin.yaml``). Sorted so a boot is reproducible
    and two plugins registering the same name always collide the same way.
    """
    if not plugins_dir.is_dir():
        return []
    return sorted(
        entry for entry in plugins_dir.iterdir()
        if entry.is_dir() and (entry / "plugin.yaml").is_file()
    )


def _disabled_names(registry: Any, candidates: list[str]) -> frozenset[str]:
    """Which of ``candidates`` the operator has explicitly DISABLED.

    ``registry.list()`` returns only enabled rows and ``registry.exists()`` knows
    both, so "known to the registry AND not enabled" is the one expression of
    disabled. A plugin with no row at all is NOT disabled — it was placed in the
    directory by hand, and Bakir's ESC-16 answer is that the directory is the
    consent.
    """
    if registry is None:
        return frozenset()
    try:
        enabled = {m.name for m in registry.list()}
        return frozenset(
            name for name in candidates
            if name not in enabled and registry.exists(name)
        )
    except Exception as exc:
        # A registry we cannot read must not decide anything. Loading what is on
        # disk is the same behaviour as having no registry at all, and it is the
        # one that keeps a working plugin working.
        log.startup.error(
            "[plugins] boot: could not read the plugin registry — enable/disable "
            "state ignored for this boot",
            exc_info=exc,
        )
        return frozenset()


async def load_installed_plugins(
    *,
    plugins_dir: Path | None,
    loader: Any,
    registry: Any = None,
    timeout_seconds: float = DEFAULT_PLUGIN_LOAD_TIMEOUT_SECONDS,
) -> PluginBootReport:
    """Import and register every installed, enabled plugin. NEVER raises.

    Returns a report rather than logging and forgetting, so the caller owns the
    boot line and a test can assert the outcome without parsing logs.
    """
    if plugins_dir is None or loader is None:
        log.startup.debug(
            "[plugins] boot: not wired — no plugins will load",
            extra={"_fields": {"has_dir": plugins_dir is not None,
                               "has_loader": loader is not None}},
        )
        return PluginBootReport()

    try:
        dirs = _installed_plugin_dirs(plugins_dir)
    except Exception as exc:
        # An unreadable plugins directory must not cost the boot. The platform
        # starts without plugins, and says so.
        log.startup.error(
            "[plugins] boot: could not read the plugins directory — starting with "
            "NO plugins loaded",
            exc_info=exc, extra={"_fields": {"dir": str(plugins_dir)}},
        )
        return PluginBootReport()

    if not dirs:
        log.startup.info(
            "[plugins] boot: no plugins installed",
            extra={"_fields": {"dir": str(plugins_dir)}},
        )
        return PluginBootReport()

    disabled = _disabled_names(registry, [d.name for d in dirs])
    loaded: list[str] = []
    skipped: list[tuple[str, str]] = []
    for plugin_dir in dirs:
        name = plugin_dir.name
        if name in disabled:
            skipped.append((name, "disabled by the operator"))
            log.startup.info(
                "[plugins] boot: plugin is disabled — not loaded",
                extra={"_fields": {"plugin": name}},
            )
            continue
        try:
            # to_thread: LocalPluginLoader.load is synchronous and IMPORTS a
            # module. Awaiting it directly would run third-party import code on
            # the event loop, so one blocking plugin would stall everything, not
            # just this load.
            await asyncio.wait_for(
                asyncio.to_thread(loader.load, plugin_dir),
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
                           "installed": len(dirs)}},
    )
    return PluginBootReport(loaded=tuple(loaded), skipped=tuple(skipped))
