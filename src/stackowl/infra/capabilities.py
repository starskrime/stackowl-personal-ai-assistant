"""Capability registry — name → :class:`HealableResource` (D05.3).

A tool declares ``ToolManifest.requires_capability = "browser"``; this module is
what turns that name into the live subsystem, so presentation can ask whether the
thing actually works before advertising it.

WHY A NAME AND NOT A PREDICATE. The reference platform gives each tool its own
``check_fn`` callable because it has no availability layer to point at. StackOwl
does: ADR-6's :class:`HealableResource` (``infra/resilience.py``) already exposes
``available`` / ``unavailable_reason`` / ``ensure_available()`` /
``register_on_recycled()``, and TEN subsystems already implement it — the browser
runtime, the four channel adapters, LanceDB, Kuzu, the embedding registry,
providers, the db pool and the MCP client. Twenty-five hand-written browser
predicates would be twenty-five wrappers around a fact the runtime already
reports.

WHY LAZY. ``ToolRegistry.with_defaults()`` registers all 77 tools at import time,
long before any runtime is constructed. Resolution therefore cannot happen at
declaration; subsystems ``register()`` themselves as the orchestrator builds them
and tools resolve by name at presentation time.

FAIL OPEN, ALWAYS. An unknown capability name yields "available" — see
:func:`resolve`. A misspelled ``requires_capability`` must present the tool, never
hide it: fail-closed would turn a typo into a silently missing toolset, which is
precisely the class of bug this item exists to end.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from collections.abc import Callable

    from stackowl.infra.resilience import HealableResource

__all__ = [
    "Availability",
    "clear",
    "invalidate_cache",
    "register",
    "registered_names",
    "resolve",
    "subscribe_to_changes",
]

#: How long a verdict is cached. Availability changes on human/daemon timescales,
#: and ``resolve`` is called once per presented tool, so re-probing every call is
#: pure waste. Mirrors the reference platform's 30s.
_TTL_SECONDS = 30.0

#: Grace period after a success during which a failure is treated as a FLAKE.
#:
#: THIS IS THE MOST IMPORTANT CONSTANT IN THE FILE, and it is borrowed scar
#: tissue rather than a guess. In the reference platform a single
#: ``docker version`` probe that timed out under load returned False once, which
#: silently stripped an entire toolset from whatever agent was being built at
#: that instant — most visibly a delegated subagent, which then reported "Tool
#: read_file does not exist" (their incidents #21658 / #5304).
#:
#: StackOwl is MORE exposed, not less: these probes run on a Jetson under load.
#: Losing 25 browser tools to one slow probe is exactly the silent capability
#: loss this repo has a standing rule against. A failure that persists past the
#: window is honoured normally, so a subsystem that really went down does stop
#: advertising its tools.
_FAILURE_GRACE_SECONDS = 60.0


@dataclass(frozen=True)
class Availability:
    """Whether a capability can run, and if not, why and what to do about it.

    A bare bool was considered and rejected: gated tools stay discoverable
    through ``tool_search``, so the model can see the tool — and a tool the model
    can see has to be able to explain itself. ``reason`` and ``remedy`` are what
    make that discovery worth keeping.
    """

    ok: bool
    reason: str | None = None
    remedy: str | None = None


_AVAILABLE = Availability(ok=True)

_lock = threading.RLock()
_resources: dict[str, HealableResource] = {}
_cache: dict[str, tuple[float, Availability]] = {}
_last_good: dict[str, float] = {}
_subscribers: list[Callable[[str], None]] = []


def register(name: str, resource: HealableResource) -> None:
    """Bind a capability name to a live resource. Called during startup wiring.

    Also hooks ``register_on_recycled`` where the resource supports it, so a
    recycle invalidates immediately instead of waiting for a poll. The two
    detection paths are complementary and neither is redundant: this callback
    fires on RECYCLE, while a capability that becomes available for the first
    time (an API key added, a daemon started) recycles nothing and is caught by
    the periodic health sweep instead.
    """
    # 1. ENTRY
    log.infra.debug(
        "[capabilities] register: entry",
        extra={"_fields": {"capability": name, "resource": type(resource).__name__}},
    )
    with _lock:
        _resources[name] = resource
        _last_good.pop(name, None)

    # Notify, do not merely drop. Re-registration is what a RECOVERED subsystem
    # looks like, so anything caching a verdict downstream — D05.2's presented-
    # tool memo above all — has to be told. Clearing only this module's own cache
    # left the memo holding the pre-recovery array for the rest of the session:
    # the capability came back and its tools stayed hidden until rollover. Caught
    # by test_a_capability_coming_back_reaches_the_next_turn, which is precisely
    # the "I fixed it and nothing happened" failure this item is meant to end.
    invalidate_cache(name)

    hook = getattr(resource, "register_on_recycled", None)
    if callable(hook):
        try:
            hook(lambda n=name: invalidate_cache(n))
        except Exception as err:  # noqa: BLE001 — wiring must not break startup
            log.infra.error(
                "[capabilities] register: on_recycled hook failed — this capability "
                "will only be re-probed by the periodic sweep",
                exc_info=err,
                extra={"_fields": {"capability": name}},
            )
    # 4. EXIT
    log.infra.debug(
        "[capabilities] register: exit",
        extra={"_fields": {"capability": name, "total": len(_resources)}},
    )


def resolve(name: str | None) -> Availability:
    """Return whether ``name`` can run right now. TTL-cached, flake-suppressed.

    FAIL OPEN on every uncertainty — no name, no registered resource, a raising
    probe with a recent success. The only verdict that hides a tool is a resource
    that is registered, reachable, and says it is unavailable.
    """
    if not name:
        return _AVAILABLE

    now = time.monotonic()
    with _lock:
        cached = _cache.get(name)
        if cached is not None and now - cached[0] < _TTL_SECONDS:
            return cached[1]
        resource = _resources.get(name)

    if resource is None:
        # 2. DECISION — unknown capability. Fail OPEN: this is what a typo in
        # requires_capability looks like, and a typo must never delete a toolset.
        # Not cached — the resource may simply not be wired yet at this point in
        # startup, and caching "unknown" would pin that for the whole TTL.
        log.infra.debug(
            "[capabilities] resolve: no resource registered — failing open",
            extra={"_fields": {"capability": name}},
        )
        return _AVAILABLE

    try:
        ok = bool(resource.available)
        reason = None if ok else (resource.unavailable_reason or "unavailable")
        remedy = None if ok else getattr(resource, "remedy", None)
    except Exception as err:  # noqa: BLE001 — a probe must never break a turn
        ok, reason, remedy = False, f"probe raised: {err}", None
        log.infra.error(
            "[capabilities] resolve: probe raised",
            exc_info=err, extra={"_fields": {"capability": name}},
        )

    with _lock:
        if ok:
            _last_good[name] = now
            verdict = _AVAILABLE
            _cache[name] = (now, verdict)
            return verdict

        last_good = _last_good.get(name)
        if last_good is not None and now - last_good < _FAILURE_GRACE_SECONDS:
            # 2. DECISION — recent success, so treat this as a FLAKE. Serve the
            # last-good verdict and do NOT cache the failure, so the next call
            # re-probes rather than pinning a stale verdict for the whole TTL.
            log.infra.warning(
                "[capabilities] resolve: probe failed within the grace window — "
                "treating as transient and KEEPING the capability available",
                extra={"_fields": {
                    "capability": name, "reason": reason,
                    "since_last_good_s": round(now - last_good, 1),
                    "grace_s": _FAILURE_GRACE_SECONDS,
                }},
            )
            return _AVAILABLE

        # No recent success, or the grace window has expired — honour it. Logged
        # at WARNING because a tool silently vanishing is undiagnosable otherwise.
        verdict = Availability(ok=False, reason=reason, remedy=remedy)
        _cache[name] = (now, verdict)
        log.infra.warning(
            "[capabilities] resolve: capability UNAVAILABLE — dependent tools "
            "will not be presented this turn",
            extra={"_fields": {"capability": name, "reason": reason, "remedy": remedy}},
        )
        return verdict


def invalidate_cache(name: str | None = None) -> None:
    """Drop a cached verdict (or all of them) and notify subscribers.

    Subscribers are how D05.2's presented-tool memo learns to rebuild: without
    that, the gate would be evaluated once per session and a newly-configured
    capability would never appear until rollover.
    """
    log.infra.debug(
        "[capabilities] invalidate_cache: entry",
        extra={"_fields": {"capability": name or "(all)"}},
    )
    with _lock:
        if name is None:
            _cache.clear()
        else:
            _cache.pop(name, None)
        subscribers = list(_subscribers)

    for callback in subscribers:
        try:
            callback(name or "")
        except Exception as err:  # noqa: BLE001 — one bad subscriber must not
            # block the others, and must not propagate into a recycle path.
            log.infra.error(
                "[capabilities] invalidate_cache: subscriber raised",
                exc_info=err, extra={"_fields": {"capability": name}},
            )


def subscribe_to_changes(callback: Callable[[str], None]) -> None:
    """Register a sync callback fired when a capability verdict is invalidated."""
    with _lock:
        _subscribers.append(callback)


def registered_names() -> list[str]:
    """Capability names currently bound to a resource (diagnostics/tests)."""
    with _lock:
        return sorted(_resources)


def clear() -> None:
    """Drop all registrations and cached verdicts. Tests only.

    SUBSCRIBERS ARE DELIBERATELY KEPT. They are wiring, not state: they are
    registered once at import (``presented_tools`` subscribes at module load) and
    there is no path that re-registers them afterwards. An earlier version
    cleared them too, which silently unhooked D05.2's memo invalidation for the
    rest of the process — every test after the first reset saw a capability
    recover and its tools stay hidden. Resetting state must not dismantle wiring.
    """
    with _lock:
        _resources.clear()
        _cache.clear()
        _last_good.clear()
