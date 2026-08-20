"""LifecycleHook — the extension point through which a plugin OBSERVES the agent.

Designed in ``docs/reference-mapping/designs/D16.1.md``, answering ESC-16 (Bakir,
2026-08-17: "yes to lifecycle hooks, as a SEPARATE capability-gated extension point
rather than a property every plugin gets; no to pip entry points").

WHAT WAS MISSING. StackOwl had six extension points and a plugin could only
REGISTER CLASSES — a tool, a job handler, a slash command, a channel, an owl
source, a memory provider. Nothing could watch the agent work. All six reference
hook names were grepped for across ``src/`` on 2026-08-15 and none existed
anywhere, so "does a plugin get lifecycle hooks?" was answered by measurement
before it was answered by design.

OBSERVE ONLY, AND THAT IS THE DECISION THAT KEEPS THIS SMALL. A hook receives a
read-only view, returns ``None``, and its return value is discarded: no veto, no
argument rewriting, no tool substitution. A ``pre_tool_call`` veto is the largest
trust surface in the item — third-party code deciding whether the agent may act —
and D17 owns security. Nothing in flight needs it and zero plugins are installed,
so a control surface for a population of zero is exactly the over-architecting
that was ruled out. Veto is a v1 NON-GOAL, to be raised as its own escalation when
a real plugin needs it.

WHAT A HOOK COSTS. Nothing, until one exists. ``dispatch`` on an unarmed point is a
dict lookup and a return — ``pre_tool_call`` fires on every tool of every turn, so
anything heavier would be paid by every user of a platform with no plugins
installed. That is also why dispatch is not logged per call: an INFO line there
would drown the log it lives in.

THREE GUARDS, EACH FOR A REASON RATHER THAN BY REFLEX (invariants I3/I4/I5 of the
design):

* A hook that RAISES is swallowed and logged at ERROR with the plugin name. The
  turn proceeds as if the hook were absent. An observer must never be able to cost
  the work it observes.
* A hook that HANGS is abandoned, not awaited. The same reasoning as the plugin
  load timeout in ``plugins/boot.py``: third-party code on a live path must never
  be able to hold the platform.
* A hook that fails REPEATEDLY is DISARMED for the life of the process and the
  operator is told at WARNING. Logging the same error on every tool call forever is
  noticing without acting, and the standing rule here is to build the actuator
  rather than file the debt. Consecutive failures, not cumulative: a hook that
  fails once an hour is annoying, one that fails every call is broken, and only the
  second should lose its place.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from stackowl.infra.observability import log

ON_SESSION_START: Final = "on_session_start"
PRE_TOOL_CALL: Final = "pre_tool_call"
POST_TOOL_CALL: Final = "post_tool_call"
PRE_LLM_CALL: Final = "pre_llm_call"
POST_LLM_CALL: Final = "post_llm_call"
ON_SESSION_END: Final = "on_session_end"

#: Every point a plugin may observe. The tuple IS the contract — a hook arms the
#: subset of these it overrides, so the manifest never carries a second list that
#: can disagree with the code.
HOOK_POINTS: Final = (
    ON_SESSION_START,
    PRE_TOOL_CALL,
    POST_TOOL_CALL,
    PRE_LLM_CALL,
    POST_LLM_CALL,
    ON_SESSION_END,
)

#: How long ONE hook gets to observe one event. Short on purpose: a hook is not
#: allowed to add perceptible latency to a turn, and anything it wants to do slowly
#: it should enqueue rather than do inline.
DEFAULT_HOOK_TIMEOUT_SECONDS: Final = 2.0

#: Consecutive failures before a hook is disarmed for the life of the process.
DEFAULT_MAX_CONSECUTIVE_FAILURES: Final = 3


class LifecycleHook:
    """Observe the agent. Change nothing.

    Override the points you care about; the ones you leave alone are never called
    and never armed. Every method receives a read-only mapping and must return
    ``None`` — a returned value is discarded, deliberately (see the module
    docstring).

    Nothing here is abstract: a hook that implements one point is a legitimate
    hook, and forcing six no-op overrides on every author would be friction with
    no safety behind it.
    """

    @property
    def hook_name(self) -> str:
        """Name used in logs. Override when the class name is not the useful one."""
        return type(self).__name__

    async def on_session_start(self, event: Mapping[str, Any]) -> None:
        """A conversation lane opened. ``session_key``, ``session_id``, ``owl_name``,
        ``channel``, ``previous_session_id`` (``None`` on a lane's first
        incarnation)."""

    async def pre_tool_call(self, event: Mapping[str, Any]) -> None:
        """A tool is about to dispatch. ``tool``, ``arguments``."""

    async def post_tool_call(self, event: Mapping[str, Any]) -> None:
        """A tool returned. ``tool``, ``success``, ``duration_ms``, ``error``."""

    async def pre_llm_call(self, event: Mapping[str, Any]) -> None:
        """A remote model round is about to run. ``provider``, ``protocol``."""

    async def post_llm_call(self, event: Mapping[str, Any]) -> None:
        """A remote model round finished. ``provider``, ``protocol``, ``ok``,
        ``duration_ms``."""

    async def on_session_end(self, event: Mapping[str, Any]) -> None:
        """A lane finalised. ``session_key``, ``session_id``, ``reason``,
        ``owl_name``, ``channel``, ``message_count``, ``completed_turns``."""


@dataclass
class _ArmedHook:
    """One registered hook and its health. Health is per HOOK, not per point: a
    plugin whose code raises will raise wherever it is called from."""

    hook: LifecycleHook
    source_name: str
    points: tuple[str, ...]
    consecutive_failures: int = 0
    disabled: bool = False


class HookRegistry:
    """Process-wide fan-out, keyed by hook point.

    Keyed rather than scanned: ``pre_tool_call`` fires on every tool of every turn,
    and walking every installed plugin to ask whether it implements this point
    would make the surface's cost grow with the number of plugins that do NOT use
    it.
    """

    _instance: HookRegistry | None = None

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_consecutive_failures = max_consecutive_failures
        self._by_point: dict[str, list[_ArmedHook]] = {p: [] for p in HOOK_POINTS}

    @classmethod
    def instance(cls) -> HookRegistry:
        """The process-wide registry, matching the ``.instance()`` idiom every other
        StackOwl registry uses."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------- registration

    @staticmethod
    def implemented_points(hook: LifecycleHook) -> tuple[str, ...]:
        """The points this hook actually overrides.

        Derived from the class rather than declared, so what a plugin observes is
        stated exactly once — in its code. A second list in the manifest would be a
        fact with two writers, and the operator would eventually be shown the one
        that had drifted.
        """
        out: list[str] = []
        for point in HOOK_POINTS:
            own = getattr(type(hook), point, None)
            base = getattr(LifecycleHook, point, None)
            if own is not None and own is not base:
                out.append(point)
        return tuple(out)

    def register(self, hook: LifecycleHook, *, source_name: str = "") -> tuple[str, ...]:
        """Arm ``hook`` on every point it implements. Returns those points.

        Signature matches the other extension-point registries (``register(instance,
        source_name=...)``) so ``LocalPluginLoader`` needs no special case for hooks.
        """
        points = self.implemented_points(hook)
        if not points:
            # Not an error — but it is certainly not what the author meant, and a
            # hook that observes nothing would otherwise look installed and working.
            log.plugins.warning(
                "[plugins] hooks.register: a hook that overrides no hook point — "
                "nothing will be observed",
                extra={"_fields": {"plugin": source_name, "hook": hook.hook_name}},
            )
            return ()
        armed = _ArmedHook(hook=hook, source_name=source_name, points=points)
        for point in points:
            self._by_point[point].append(armed)
        log.plugins.info(
            "[plugins] hooks.register: exit",
            extra={"_fields": {"plugin": source_name, "hook": hook.hook_name,
                               "points": list(points), "count": len(points)}},
        )
        return points

    def unregister_source(self, source_name: str) -> int:
        """Drop every hook a plugin registered. Mirrors how the loader drops its
        classes — a hook outliving its plugin is third-party code running after it
        was uninstalled. Returns how many hooks were dropped."""
        dropped = 0
        for point, armed_list in self._by_point.items():
            keep = [a for a in armed_list if a.source_name != source_name]
            dropped += len(armed_list) - len(keep)
            self._by_point[point] = keep
        if dropped:
            log.plugins.info(
                "[plugins] hooks.unregister: exit",
                extra={"_fields": {"plugin": source_name, "dropped": dropped}},
            )
        return dropped

    def has(self, point: str) -> bool:
        """True when at least one live hook is armed on ``point``.

        The fast path every call site uses: with no plugins installed this is the
        whole cost of the hook surface.
        """
        return bool(self._by_point.get(point))

    # ------------------------------------------------------------------ dispatch

    async def dispatch(self, point: str, payload: Mapping[str, Any]) -> None:
        """Fan ``payload`` out to every hook armed on ``point``. NEVER raises.

        Returns ``None`` whatever a hook returns, so no caller can grow a veto by
        accident — observe-only is enforced here rather than trusted.
        """
        armed_list = self._by_point.get(point)
        if not armed_list:
            return
        try:
            view: Mapping[str, Any] = MappingProxyType(dict(payload))
        except Exception as exc:  # noqa: BLE001 — a bad payload must not cost the turn
            log.plugins.error(
                "[plugins] hooks.dispatch: event payload unusable — hooks skipped",
                exc_info=exc, extra={"_fields": {"point": point}},
            )
            return
        for entry in list(armed_list):
            if entry.disabled:
                continue
            try:
                await asyncio.wait_for(
                    getattr(entry.hook, point)(view), timeout=self._timeout_seconds
                )
            except TimeoutError as exc:  # asyncio.TimeoutError is this, since 3.11
                self._record_failure(entry, point, exc, timed_out=True)
            except Exception as exc:  # noqa: BLE001 — I3: a hook never costs the turn
                self._record_failure(entry, point, exc, timed_out=False)
            else:
                entry.consecutive_failures = 0

    def _record_failure(
        self, entry: _ArmedHook, point: str, exc: BaseException, *, timed_out: bool
    ) -> None:
        entry.consecutive_failures += 1
        log.plugins.error(
            "[plugins] hooks.dispatch: hook TIMED OUT — abandoned"
            if timed_out
            else "[plugins] hooks.dispatch: hook failed — the turn continues without it",
            exc_info=exc,
            extra={"_fields": {
                "plugin": entry.source_name, "hook": entry.hook.hook_name,
                "point": point, "consecutive_failures": entry.consecutive_failures,
                "timeout_s": self._timeout_seconds if timed_out else None,
            }},
        )
        if entry.consecutive_failures < self._max_consecutive_failures:
            return
        entry.disabled = True
        for armed_point in entry.points:
            self._by_point[armed_point] = [
                a for a in self._by_point[armed_point] if a is not entry
            ]
        log.plugins.warning(
            "[plugins] hooks.disable: exit — hook DISARMED for the life of this "
            "process after repeated failures; reinstall or fix the plugin",
            extra={"_fields": {
                "plugin": entry.source_name, "hook": entry.hook.hook_name,
                "consecutive_failures": entry.consecutive_failures,
                "points": list(entry.points),
            }},
        )


async def dispatch(point: str, payload: Mapping[str, Any]) -> None:
    """Dispatch on the process-wide registry, cheaply.

    The one call every seam in the platform uses. It exists so a call site carries
    a single import and no registry plumbing, and so the "no plugins installed"
    path is one dict lookup rather than a construction.
    """
    registry = HookRegistry.instance()
    if not registry.has(point):
        return
    await registry.dispatch(point, payload)
