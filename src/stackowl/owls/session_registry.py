"""SessionRegistry — named persistent owl sessions (E8-S3).

A :class:`SessionRegistry` is a small, narrow DI singleton (constructed in the
startup assembly, injected onto ``StepServices.session_registry`` — never
module-level, ARCH-88). It owns ONLY the ``label → SessionHandle`` map plus the
lifecycle (spawn / touch / reap); it deliberately does NOT duplicate
:class:`stackowl.messaging.a2a.A2AQueue` mailboxing — when a session is cleared or
reaped it asks the A2AQueue to DRAIN that session's mailbox so orphaned inter-owl
messages don't leak (the queue has no eviction of its own).

Continuity is NOT stored on the handle. A session's conversation is threaded
THROUGH the existing Plan A history system: ``sessions_spawn`` / ``sessions_send``
run the pipeline with ``session_id=f"session:{label}"``; ``classify`` reads prior
turns from the MemoryBridge by that id (overwriting any seed) and ``consolidate``
writes each turn back to the bridge under that id. The handle therefore carries
ONLY identity + activity — no ``history`` (a duplicated handle-history would be
dead, silently overwritten by classify's bridge read).

Self-healing: every method is bounded and structured — a duplicate label raises
a typed :class:`SessionRegistryError` the tool surfaces as a structured refusal
(never a fake-success); the TTL sweep reaps idle sessions so a dead handle is
never a stuck one; draining on clear/reap prevents a mailbox leak. Clock-injected
(ARCH-99) so the idle-TTL is deterministically testable by advancing a fake
clock. No live-session count cap (owner decision 2026-07-22).
"""

from __future__ import annotations

from threading import Lock

from pydantic import BaseModel, ConfigDict

from stackowl.exceptions import StackOwlError
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.delegation_limits import SESSION_IDLE_TTL_SECONDS


class SessionRegistryError(StackOwlError):
    """A structured session-lifecycle failure (duplicate label / capacity).

    A :class:`StackOwlError` so the ``sessions_spawn`` tool's ``except`` already
    catching it degrades to a structured refusal rather than crashing — never a
    fake-success, never a raise out of the tool.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"session {reason}: {detail}")


class SessionHandle(BaseModel):
    """A single named, persistent owl session — identity + activity only.

    Frozen; ``last_active`` evolves via :meth:`with_active` (model_copy) so the
    handle stays an immutable value the registry swaps in its map rather than
    mutating in place. Conversation continuity is NOT here — it lives in the
    MemoryBridge under ``session:{label}`` (see module docstring).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    owl_name: str
    created_at: float  # monotonic seconds at spawn
    last_active: float  # monotonic seconds of the most recent touch

    def with_active(self, now: float) -> SessionHandle:
        """Return a copy with ``last_active`` bumped to ``now`` (monotonic)."""
        return self.model_copy(update={"last_active": now})


class SessionRegistry:
    """Holds named owl sessions; bounds idle lifetime; drains on reap.

    No live-session COUNT cap (owner decision 2026-07-22 — see spawn())."""

    def __init__(
        self,
        *,
        a2a_queue: A2AQueue | None = None,
        clock: Clock | None = None,
        idle_ttl_seconds: float = SESSION_IDLE_TTL_SECONDS,
    ) -> None:
        self._sessions: dict[str, SessionHandle] = {}
        self._a2a_queue = a2a_queue
        self._clock: Clock = clock or WallClock()
        self._idle_ttl = idle_ttl_seconds
        self._lock = Lock()
        log.engine.debug(
            "[sessions] __init__: entry",
            extra={"_fields": {"idle_ttl_s": idle_ttl_seconds,
                               "has_queue": a2a_queue is not None}},
        )

    # --------------------------------------------------------------- spawn
    def spawn(self, label: str, owl_name: str) -> SessionHandle:
        """Register a new named session; refuse a duplicate label.

        Raises :class:`SessionRegistryError` (structured, caught by the tool) on a
        duplicate label — the caller MUST learn it collided rather than silently
        reuse. No live-session COUNT cap (owner decision 2026-07-22 — was a guess
        at normal usage, not a measured pathology); ``SESSION_IDLE_TTL_SECONDS``
        still reaps abandoned sessions so growth isn't unbounded over time.
        """
        # 1. ENTRY
        log.engine.debug(
            "[sessions] spawn: entry",
            extra={"_fields": {"label": label, "owl": owl_name, "live": len(self._sessions)}},
        )
        with self._lock:
            # 2. DECISION — duplicate label is an explicit collision, not a reuse.
            if label in self._sessions:
                log.engine.warning(
                    "[sessions] spawn: duplicate label — refusing",
                    extra={"_fields": {"label": label, "owl": owl_name}},
                )
                raise SessionRegistryError(
                    "duplicate_label",
                    f"a session labelled {label!r} already exists; choose another label "
                    "or clear the existing one first.",
                )
            now = self._clock.monotonic()
            handle = SessionHandle(
                label=label, owl_name=owl_name, created_at=now, last_active=now,
            )
            self._sessions[label] = handle
        # 4. EXIT
        log.engine.info(
            "[sessions] spawn: exit",
            extra={"_fields": {"label": label, "owl": owl_name, "live": len(self._sessions)}},
        )
        return handle

    # ----------------------------------------------------------- accessors
    def get(self, label: str) -> SessionHandle | None:
        """Return the handle for ``label``, or ``None`` if no such session."""
        return self._sessions.get(label)

    def all(self) -> list[SessionHandle]:
        """Return every live session handle (snapshot copy)."""
        return list(self._sessions.values())

    def touch(self, label: str) -> SessionHandle | None:
        """Bump ``last_active`` for ``label`` so the idle sweep spares it.

        Returns the refreshed handle, or ``None`` if the session is gone (a
        no-op, never raising — a reaped label touched late is not an error).
        """
        log.engine.debug("[sessions] touch: entry", extra={"_fields": {"label": label}})
        with self._lock:
            current = self._sessions.get(label)
            if current is None:
                log.engine.debug("[sessions] touch: unknown label", extra={"_fields": {"label": label}})
                return None
            refreshed = current.with_active(self._clock.monotonic())
            self._sessions[label] = refreshed
        return refreshed

    # ------------------------------------------------------------ lifecycle
    def clear_session(self, label: str) -> bool:
        """Remove ``label`` and DRAIN its A2A mailbox. True if it existed.

        Draining (via :meth:`A2AQueue.drain`) discards any orphaned inter-owl
        messages addressed to this session so the mailbox doesn't leak after the
        handle is gone. Never raises — a missing session clears to ``False``.
        """
        log.engine.debug("[sessions] clear_session: entry", extra={"_fields": {"label": label}})
        with self._lock:
            handle = self._sessions.pop(label, None)
        if handle is None:
            log.engine.debug("[sessions] clear_session: unknown label", extra={"_fields": {"label": label}})
            return False
        self._drain_if_orphaned(handle.owl_name)  # owl-keyed mailbox: see helper
        log.engine.info(
            "[sessions] clear_session: exit",
            extra={"_fields": {"label": label, "owl": handle.owl_name, "live": len(self._sessions)}},
        )
        return True

    def sweep(self, now: float | None = None) -> int:
        """Reap every session idle past the TTL, draining each. Return reap count.

        ``now`` defaults to the injected clock's monotonic time (production); a
        test passes an advanced value to drive the idle boundary deterministically.
        Never raises into a scheduler loop — a drain failure is logged, not thrown.
        """
        moment = self._clock.monotonic() if now is None else now
        # 1. ENTRY
        log.engine.debug(
            "[sessions] sweep: entry",
            extra={"_fields": {"now": moment, "live": len(self._sessions), "ttl_s": self._idle_ttl}},
        )
        with self._lock:
            expired = [
                h for h in self._sessions.values() if (moment - h.last_active) >= self._idle_ttl
            ]
            for handle in expired:
                self._sessions.pop(handle.label, None)
        for handle in expired:
            # 3. STEP — drain a reaped session's mailbox ONLY when no live session
            # still shares that owl (else we'd eat a live same-owl session's mail).
            self._drain_if_orphaned(handle.owl_name)
        # 4. EXIT
        if expired:
            log.engine.info(
                "[sessions] sweep: exit — reaped idle sessions",
                extra={"_fields": {"reaped": len(expired),
                                   "labels": [h.label for h in expired],
                                   "live": len(self._sessions)}},
            )
        else:
            log.engine.debug("[sessions] sweep: exit — nothing idle", extra={"_fields": {"live": len(self._sessions)}})
        return len(expired)

    def clear_all(self) -> int:
        """Remove every session, draining each mailbox. Return the count cleared.

        Called on shutdown so no session (or its mailbox) outlives the process.
        """
        log.engine.debug("[sessions] clear_all: entry", extra={"_fields": {"live": len(self._sessions)}})
        with self._lock:
            handles = list(self._sessions.values())
            self._sessions.clear()
        for handle in handles:
            self._drain_mailbox(handle.owl_name)
        log.engine.info("[sessions] clear_all: exit", extra={"_fields": {"cleared": len(handles)}})
        return len(handles)

    # --------------------------------------------------------------- helpers
    def _drain_if_orphaned(self, owl_name: str) -> None:
        """Drain ``owl_name``'s mailbox ONLY if no live session still uses that owl.

        Sessions are label-keyed but mailboxes are owl-keyed: a blind drain would
        eat a live same-owl session's mail. Drained when the LAST same-owl session
        clears — no leak meanwhile (the owl is still reachable).
        """
        with self._lock:
            still_used = any(h.owl_name == owl_name for h in self._sessions.values())
        if still_used:
            log.engine.debug(
                "[sessions] _drain_if_orphaned: owl still in use — skip drain",
                extra={"_fields": {"owl": owl_name}},
            )
            return
        self._drain_mailbox(owl_name)

    def _drain_mailbox(self, owl_name: str) -> None:
        """Drain ``owl_name``'s A2A mailbox; log + swallow (B5) so reap never throws."""
        if self._a2a_queue is None:
            log.engine.debug(
                "[sessions] _drain_mailbox: no a2a_queue wired — skip",
                extra={"_fields": {"owl": owl_name}},
            )
            return
        try:
            discarded = self._a2a_queue.drain(owl_name)
            log.engine.debug(
                "[sessions] _drain_mailbox: drained",
                extra={"_fields": {"owl": owl_name, "discarded": discarded}},
            )
        except Exception as exc:  # B5 — a drain failure must never wedge a reap.
            log.engine.error(
                "[sessions] _drain_mailbox: drain failed — continuing",
                exc_info=exc,
                extra={"_fields": {"owl": owl_name}},
            )
