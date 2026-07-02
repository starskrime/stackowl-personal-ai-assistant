"""StickyRouteCache — FR-9 per-session sticky-routing cache.

A small in-memory, per-session TTL cache mapping ``session_id -> (owl_name,
intent_class, resolved_at)``. Consulted by ``pipeline/steps/triage.py`` to
skip the LLM :class:`~stackowl.owls.router.SecretaryRouter` call on short,
same-session follow-ups (FR-9) — the mechanical bypass rule PRD FR-9
specifies, guarded by counter-metric CM-2 (misroute regressions).

Purpose-built rather than reusing an existing store: neither ``TurnRegistry``
(``gateway/turn_registry.py``) nor ``SessionRegistry`` (``owls/session_registry.py``,
the unrelated named-persistent-sessions feature) track per-session owl
resolution, and ``state.history`` isn't populated until AFTER triage runs.

``time.monotonic()`` is used for ``resolved_at`` — matches this codebase's
existing pattern (:class:`~stackowl.owls.session_registry.SessionHandle`'s
``created_at``/``last_active``): immune to wall-clock jumps, and a gateway
restart naturally clears the in-memory dict anyway so wall-clock persistence
isn't needed. No thread lock — consulted only from the async pipeline's
single event loop (same assumption ``TurnRegistry``'s per-session dicts
already make).

This is a soft cache, not a correctness-critical store: a stale/missing entry
just falls through to the normal LLM router path, never a hard failure.
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log

TTL_SECONDS = 300  # 5 minutes. Adversarial review (2026-07-01): 30 min is long
# enough that topic drift (checked phone, got interrupted) is the common case,
# not the exception, in real chat usage — shrunk to a window where "still on
# the same topic" is a defensible default. Tune against CM-2.


class StickyRouteCache:
    """Per-session ``(owl_name, intent_class)`` cache with a fixed TTL.

    Callers MUST NOT call :meth:`set` with ``intent_class`` other than
    ``"conversational"``. Two reasons, both from the 2026-07-01 adversarial
    review: a ``"clarify"`` resolution means the router was uncertain last
    turn (reusing it is nonsensical, and its ``clarify_question`` is
    turn-specific and would be stale); a ``"standard"`` (work-turn) resolution
    is the one most likely to be stale by the time a short follow-up arrives,
    and reusing it silently defeats the F120 tool-capability gate + the
    answer-floor tier (both key off ``intent_class``). Not enforced here (the
    triage.py call site simply never passes anything else) — kept as a
    general-purpose cache class rather than baking the restriction in, but the
    restriction is load-bearing at the call site, not optional polish.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, str, float]] = {}

    def get(self, session_id: str) -> tuple[str, str] | None:
        """Return ``(owl_name, intent_class)`` if a fresh entry exists, else None."""
        log.engine.debug(
            "[sticky_route_cache] get: entry",
            extra={"_fields": {"session_id": session_id}},
        )
        entry = self._entries.get(session_id)
        if entry is None:
            log.engine.debug(
                "[sticky_route_cache] get: miss",
                extra={"_fields": {"session_id": session_id}},
            )
            return None
        owl_name, intent_class, resolved_at = entry
        age = time.monotonic() - resolved_at
        if age >= TTL_SECONDS:
            log.engine.debug(
                "[sticky_route_cache] get: stale — expired",
                extra={"_fields": {"session_id": session_id, "age_s": age}},
            )
            return None
        log.engine.debug(
            "[sticky_route_cache] get: hit",
            extra={
                "_fields": {
                    "session_id": session_id,
                    "owl": owl_name,
                    "intent_class": intent_class,
                    "age_s": age,
                }
            },
        )
        return owl_name, intent_class

    def set(self, session_id: str, owl_name: str, intent_class: str) -> None:
        """Write/overwrite ``session_id``'s entry with the current monotonic time."""
        log.engine.debug(
            "[sticky_route_cache] set: entry",
            extra={
                "_fields": {
                    "session_id": session_id,
                    "owl": owl_name,
                    "intent_class": intent_class,
                }
            },
        )
        self._entries[session_id] = (owl_name, intent_class, time.monotonic())
        log.engine.debug(
            "[sticky_route_cache] set: exit",
            extra={"_fields": {"session_id": session_id, "live": len(self._entries)}},
        )
