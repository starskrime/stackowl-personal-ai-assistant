from __future__ import annotations

import asyncio
import enum
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from stackowl.infra.observability import log

_MAILBOX_MAX = 8

# F-67 — a turn that WEDGES (task done but status never reached DONE, or expired)
# has its own goal re-enqueued by the sweep so it is not silently lost. Bound the
# re-dispatch so a genuinely-poisonous turn that keeps wedging cannot loop forever:
# after this many wedge-driven re-dispatches the lineage is given up (logged, not
# re-enqueued). The lineage is carried in the re-enqueued request_id suffix.
_MAX_WEDGE_REDISPATCH = 2
_REDISPATCH_SUFFIX = "-redispatch-"

# Per-session intake queue bound (FIFO mailbox depth). Past this, ``enqueue``
# raises ``QueueFull`` and the orchestrator rejects-with-notice (coalesce-oldest
# is the §4.7 backlog alternative). Kept modest: a single chat backing up dozens
# of queued turns is already a misuse signal, not normal flow.
_DEFAULT_PER_SESSION_QUEUE_MAX = 8


class QueueFull(Exception):
    """Per-session intake queue is at its bound — loud overflow, never silent growth."""


def default_global_running_max() -> int:
    """Host-derived ceiling on concurrent turns across ALL sessions.

    No Jetson-pinned constant (all-hardware rule): scale to the host. There is
    no dedicated host capability probe in the tree, so we derive from
    ``os.cpu_count()`` — concurrent turns are LLM/IO-bound coordination work, so
    we allow a small multiple of the CPU count, floored so even a 1-core box can
    make progress and capped to avoid an unbounded fan-out on huge hosts. A
    config override is exposed via the ``TurnRegistry`` ctor.
    """
    cpus = os.cpu_count() or 1
    return max(4, min(cpus * 4, 64))


def default_turn_ttl_seconds() -> float:
    """Host-scaled backstop TTL for the turn sweeper (F050), no Jetson pin.

    A GENEROUS backstop — the common wedge (a turn whose task is ``done()`` but
    status never reached ``DONE``) is caught by the done-leg of ``sweep`` within
    one sweep interval regardless of this value; the TTL only bounds the
    pathological case of a turn that never completes at all. Floored high (order of
    an hour) so a slow box never reaps a turn that is merely taking a long time,
    and the floor scales nothing down on small hardware (all-hardware rule). The
    expired leg NEVER reaps a still-running (``not task.done()``) turn — TTL only
    tightens *when* a done-stuck turn is reaped.
    """
    return 3600.0


class TurnStatus(enum.Enum):
    RUNNING = "running"
    FINALIZING = "finalizing"
    DONE = "done"


# legal one-way transitions
_NEXT: dict[TurnStatus, TurnStatus] = {
    TurnStatus.RUNNING: TurnStatus.FINALIZING,
    TurnStatus.FINALIZING: TurnStatus.DONE,
}


@dataclass
class PendingIntake:
    request_id: str
    original_input: str
    # String targets for Slack (channel id / thread_ts); int for Telegram chat_id.
    target: int | str | None


@dataclass
class Turn:
    turn_id: str  # == request_id
    session_id: str
    task: asyncio.Task[None] | None
    # String targets for Slack (channel id / thread_ts); int for Telegram chat_id.
    target: int | str | None
    original_input: str
    status: TurnStatus = TurnStatus.RUNNING
    stop_requested: bool = False
    clarify_pending: bool = False
    steering_mailbox: asyncio.Queue[str] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_MAILBOX_MAX)
    )
    started_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TurnRegistry:
    """In-memory per-session turn tracking: one running turn + FIFO intake queue."""

    def __init__(
        self,
        *,
        per_session_queue_max: int = _DEFAULT_PER_SESSION_QUEUE_MAX,
        global_running_max: int | None = None,
    ) -> None:
        self._per_session_queue_max = max(1, per_session_queue_max)
        self._global_running_max = (
            default_global_running_max() if global_running_max is None else max(1, global_running_max)
        )
        self._turns: dict[str, Turn] = {}            # request_id -> Turn
        self._running: dict[str, str] = {}           # session_id -> request_id
        self._queues: dict[str, deque[PendingIntake]] = {}
        # Per-session intake lock (lazily created, stable per session). It makes
        # the "decide dispatch-vs-enqueue and claim the running slot" critical
        # section mutually exclusive between the orchestrator's _intake and the
        # detached _drain_next: _drain_next holds it across its
        # resolve_or_rewrite await (the classifier yield), so a fresh same-session
        # _intake BLOCKS on the lock until drain has re-registered (or consumed
        # the queued message) instead of seeing a transiently-IDLE session and
        # starting a SECOND running turn. Cross-session uses different locks and
        # is untouched. Holding across the LLM await is correct: same-session
        # intake is serialized BY DESIGN (≤1 running turn per session).
        self._intake_locks: dict[str, asyncio.Lock] = {}
        # F050 — drain hook fired by ``sweep`` after a reap frees a _running slot,
        # so a reaped-but-stranded session (queued intake, no running turn) is
        # surfaced to the global-cap drain seam (no fake success: reaped AND
        # surfaced). Wired by the orchestrator at startup; None in unit tests.
        self._on_stranded: Callable[[], Awaitable[None]] | None = None
        # STEER-3/F057 — eviction hook fired by ``sweep`` with the reaped
        # request_ids, so a reaped (wedged/GC'd) turn's parked raw IngressMessage
        # is reclaimed (the ParkedIntakes map otherwise only pops on a successful
        # drain → a slow leak). Wired by the orchestrator at startup; None in unit
        # tests. SYNCHRONOUS (pure bookkeeping over an in-memory dict, no await).
        self._on_reaped: Callable[[list[str]], int] | None = None

    def set_stranded_drainer(self, cb: Callable[[], Awaitable[None]] | None) -> None:
        """Register the post-reap drain callback (F050 stranded-session surfacing)."""
        self._on_stranded = cb

    def set_reaped_evictor(self, cb: Callable[[list[str]], int] | None) -> None:
        """Register the post-reap parked-intake evictor (STEER-3/F057 leak guard)."""
        self._on_reaped = cb

    @property
    def per_session_queue_max(self) -> int:
        return self._per_session_queue_max

    @property
    def global_running_max(self) -> int:
        return self._global_running_max

    def active_turn_count(self) -> int:
        """Number of turns currently RUNNING across all sessions (FIFO-queued
        turns not yet started are not counted — they impose no live CPU load)."""
        return len(self._running)

    def has_active_turns(self) -> bool:
        """True when any user turn is mid-flight — the signal heavy background
        jobs consult to defer themselves and stop starving the foreground turn."""
        return bool(self._running)

    def at_global_capacity(self) -> bool:
        """True when the number of running turns across ALL sessions is at the cap.

        The orchestrator consults this BEFORE dispatching a new turn: at capacity,
        the new turn is held/queued (bounded wait) rather than silently dropped or
        crashing the box. Loudly logged at the call site and here.
        """
        at_cap = len(self._running) >= self._global_running_max
        if at_cap:
            log.gateway.warning(
                "[turn] at global capacity — new turns must wait",
                extra={
                    "_fields": {
                        "running": len(self._running),
                        "global_running_max": self._global_running_max,
                    }
                },
            )
        return at_cap

    def session_intake_lock(self, session_id: str) -> asyncio.Lock:
        """Return the stable per-session intake lock (created on first use).

        Must be created lazily on the running event loop (an ``asyncio.Lock``
        binds to the loop where it is first awaited), so it is built here on
        demand rather than eagerly in ``__init__``.
        """
        lock = self._intake_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._intake_locks[session_id] = lock
        return lock

    def get(self, request_id: str) -> Turn | None:
        return self._turns.get(request_id)

    def request_stop(self, request_id: str) -> None:
        """Set a running turn's cooperative-stop FLAG (concurrent-msg §5.3).

        A 'stop' steer routes here. We set ``turn.stop_requested = True`` — a FLAG,
        NEVER ``task.cancel()`` (a cancel raises mid-tool → torn state). The running
        ReAct loop's iteration-boundary callback (``make_steering_callback``) reads
        the flag at its NEXT boundary, after the current tool batch is fully
        observed, and raises ``TurnStopped`` to finalize gracefully. Stop is
        cooperative at iteration granularity (bounded latency: it cannot interrupt a
        long in-flight tool — documented, not a bug).

        Fail-safe: an unknown ``request_id`` (already deregistered / never
        registered) is a no-op (logged, never raised) — a stop on a turn that has
        already finished is harmless.
        """
        turn = self._turns.get(request_id)
        if turn is None:
            log.gateway.debug(
                "[turn] request_stop: no live turn — no-op",
                extra={"_fields": {"request_id": request_id}},
            )
            return
        turn.stop_requested = True
        log.gateway.info(
            "[turn] request_stop: stop flag set (cooperative, NOT cancel)",
            extra={"_fields": {"request_id": request_id, "session_id": turn.session_id}},
        )

    def running(self, session_id: str) -> Turn | None:
        rid = self._running.get(session_id)
        return self._turns.get(rid) if rid else None

    @staticmethod
    def _put_steer_superseding(turn: Turn, text: str) -> None:
        """Put a steer onto the turn's mailbox, superseding the oldest if FULL.

        §5.4 bounded-mailbox + supersede-oldest backpressure. The mailbox is a
        bounded ``asyncio.Queue(maxsize=_MAILBOX_MAX)`` — under steer-spam it must
        NOT reject the newest steer (the user's latest instruction is the most
        relevant) NOR grow unbounded. So on a FULL mailbox we DROP the OLDEST
        pending steer (``get_nowait()``) and then ``put_nowait(text)`` the newest —
        the newest steer ALWAYS lands, the mailbox stays bounded. The drained side
        (``make_steering_callback``) still folds ALL pending into ONE coalesced
        ``[steering]`` message, so the LLM context window is never blown by N
        separate messages.

        CALLER CONTRACT: when used from ``try_steer``'s RUNNING branch, ``turn.lock``
        MUST be held so the status-read and the put stay ONE atomic critical
        section (Task 11 lost-steer atomicity). The drop-then-put is also done here
        with no intervening ``await`` so it cannot interleave with a concurrent
        drain even when called lock-free (the ``put_steer`` synchronous path).

        Fail-safe: if even after dropping the oldest the put still raises
        ``QueueFull`` (a concurrent producer refilled the freed slot — only possible
        on the lock-free path), the steer is dropped LOUD (logged as a lost
        instruction), never silently, and never raised at the caller.
        """
        try:
            turn.steering_mailbox.put_nowait(text)
            return
        except asyncio.QueueFull:
            pass
        # FULL — supersede the oldest pending steer, then accept the newest.
        try:
            superseded = turn.steering_mailbox.get_nowait()
        except asyncio.QueueEmpty:
            superseded = None
        log.gateway.info(
            "[turn] steering mailbox full — superseding oldest steer (§5.4)",
            extra={"_fields": {
                "request_id": turn.turn_id,
                "session_id": turn.session_id,
                "superseded": superseded is not None,
                "maxsize": turn.steering_mailbox.maxsize,
            }},
        )
        try:
            turn.steering_mailbox.put_nowait(text)
        except asyncio.QueueFull as exc:
            # Slot refilled by a concurrent producer between get and put (lock-free
            # path only). Loud, never silent — the newest steer is dropped here but
            # the mailbox is still bounded and a co-arriving steer survives.
            log.gateway.error(
                "[turn] steering mailbox still full after supersede — newest steer DROPPED",
                exc_info=exc,
                extra={"_fields": {
                    "request_id": turn.turn_id, "session_id": turn.session_id,
                }},
            )

    def put_steer(self, request_id: str, text: str) -> None:
        """TEST-ONLY / future-API: lock-free synchronous steer-put helper.

        NOT wired into the production steer path — the live route is
        ``try_steer`` → ``_put_steer_superseding`` (called under ``turn.lock`` so
        the status-read and put stay one atomic critical section, Task 11 lost-steer
        atomicity). This wrapper exists so the bounded-mailbox + supersede-oldest
        invariant can be exercised lock-free in tests, and as the documented entry
        for a future lock-free producer. The ``QueueFull``-after-supersede defensive
        branch in ``_put_steer_superseding`` exists for exactly this lock-free
        contract (it is unreachable on the lock-held live path). Search before wiring
        this in production: a lock-free producer must accept the documented
        newest-dropped-LOUD fail-safe.

        §5.4 — bounded mailbox + supersede-oldest. Resolves the turn by
        ``request_id`` and folds ``text`` in via ``_put_steer_superseding`` (drop the
        oldest pending steer on a FULL mailbox, accept the newest). Synchronous (no
        ``await``) so the drop-then-put is atomic vs. the loop's drain without a
        lock.

        Fail-safe: an unknown ``request_id`` (already deregistered / never
        registered) is a no-op (logged, never raised) — a steer on a finished turn
        is harmless here (the live steer-routing decision is made in ``try_steer``).
        """
        turn = self._turns.get(request_id)
        if turn is None:
            log.gateway.debug(
                "[turn] put_steer: no live turn — no-op",
                extra={"_fields": {"request_id": request_id}},
            )
            return
        self._put_steer_superseding(turn, text)

    async def register(
        self,
        request_id: str,
        *,
        session_id: str,
        task: asyncio.Task[None] | None,
        target: int | str | None,
        original_input: str,
    ) -> Turn:
        turn = Turn(
            turn_id=request_id,
            session_id=session_id,
            task=task,
            target=target,
            original_input=original_input,
        )
        self._turns[request_id] = turn
        self._running[session_id] = request_id
        log.gateway.debug(
            "[turn] register",
            extra={"_fields": {"request_id": request_id, "session_id": session_id}},
        )
        return turn

    async def try_steer(
        self,
        request_id: str,
        text: str,
        *,
        session_id: str,
        request_id_new: str,
        target: int | str | None,
    ) -> str:
        """Atomically route a steer to a RUNNING turn, or convert it to a new turn.

        §9 invariant 1 (lost-steer guard) — the enqueue side. A steer must NEVER
        land in a dead turn's mailbox. Under the turn's per-turn ``lock`` (so the
        status-read and the put are ONE atomic critical section vs.
        ``finalize_and_drain`` — the SOLE completion-seam guard — which takes the
        same lock):

          * status ``RUNNING`` → fold the steer onto ``steering_mailbox`` with
            supersede-oldest backpressure (``_put_steer_superseding``) and return
            ``"STEER"`` (the live loop will fold it at its next iteration boundary).
            §5.4: on a FULL mailbox we DROP the OLDEST pending steer and accept the
            newest — bounded + newest-always-lands — rather than spawning a NEW
            turn. (Reconciles with the earlier Task 11 "NEW on full" path: that
            fallback is the policy for a turn past its finalization line; for a
            RUNNING turn the §5.4 supersede-oldest policy applies so steer-spam
            stays a single live turn, never a fan-out of queued-new turns.)
          * status ``FINALIZING``/``DONE`` (turn is past its finalization line) →
            ``enqueue`` the text as a queued-new turn and return ``"NEW"`` (the
            caller dispatches it as a fresh turn — never onto the dead mailbox).

        Fail-safe: an unknown ``request_id`` (already deregistered) is treated as
        past-finalization → convert to a queued-new turn, return ``"NEW"`` (a
        discarded steer is a lost instruction, never silently dropped).
        """
        turn = self._turns.get(request_id)
        if turn is None:
            # No live turn → already past its finalization line. Convert.
            log.gateway.debug(
                "[turn] try_steer: no live turn — converting to queued-new",
                extra={"_fields": {
                    "request_id": request_id, "request_id_new": request_id_new,
                    "session_id": session_id,
                }},
            )
            self.enqueue(
                session_id, original_input=text, request_id=request_id_new, target=target
            )
            return "NEW"
        async with turn.lock:
            # Status-read + put-or-convert ATOMIC under the lock (no window where
            # status reads RUNNING but the put lands after FINALIZING).
            if turn.status is TurnStatus.RUNNING:
                # §5.4 supersede-oldest: on a FULL mailbox drop the OLDEST pending
                # steer and accept the newest (bounded + newest-always-lands), NOT a
                # NEW turn. Atomic with the status-read above (we hold turn.lock and
                # _put_steer_superseding does no await), so the lost-steer atomicity
                # vs. finalize_and_drain (the sole completion-seam guard) is preserved.
                self._put_steer_superseding(turn, text)
                log.gateway.debug(
                    "[turn] try_steer: accepted by RUNNING turn (supersede-oldest if full)",
                    extra={"_fields": {"request_id": request_id, "session_id": session_id}},
                )
                return "STEER"
            # FINALIZING / DONE — past the finalization line; never enqueue onto
            # the dead mailbox. Convert to a queued-new turn.
            log.gateway.debug(
                "[turn] try_steer: turn past finalization — converting to queued-new",
                extra={"_fields": {
                    "request_id": request_id, "request_id_new": request_id_new,
                    "session_id": session_id, "status": turn.status.value,
                }},
            )
            self.enqueue(
                session_id, original_input=text, request_id=request_id_new, target=target
            )
            return "NEW"

    def _reroute_survivors_locked(self, turn: Turn, survivors: list[str]) -> None:
        """Re-route already-drained mailbox survivors as queued-new turns.

        MUST be called with ``turn.lock`` HELD (it mutates the intake queue as part
        of the same atomic teardown critical section). Each survivor becomes a
        queued-new turn (FIFO, inheriting the turn's ``session_id``/``target``) with
        a fresh request id derived from the turn id + ordinal so the orchestrator's
        queued-new dispatch keys them uniquely. Used by ``finalize_and_drain`` (the
        SOLE lost-steer completion-seam guard) so the enqueue-as-queued-new logic
        lives in ONE place. A full intake queue is loud-but-non-fatal (logged, never
        raised) —
        the survivor stays in the caller's returned list so it is still visible.
        """
        for i, text in enumerate(survivors):
            try:
                self.enqueue(
                    turn.session_id,
                    original_input=text,
                    request_id=f"{turn.turn_id}-survivor-{i}",
                    target=turn.target,
                )
                log.gateway.info(
                    "[turn] survivor steer re-routed as queued-new",
                    extra={"_fields": {
                        "request_id": turn.turn_id, "session_id": turn.session_id,
                        "survivor_index": i,
                    }},
                )
            except QueueFull as exc:
                # The intake queue is full — the survivor cannot be re-routed.
                # Loud, never silent: the steer is dropped but logged as a lost
                # instruction so it is visible (the alternative — unbounded queue
                # growth — is worse). Remaining survivors stay in the returned list
                # so the caller still sees them.
                log.gateway.error(
                    "[turn] survivor steer re-route failed — intake queue full, DROPPED",
                    exc_info=exc,
                    extra={"_fields": {
                        "request_id": turn.turn_id, "session_id": turn.session_id,
                        "survivor_index": i,
                    }},
                )

    @staticmethod
    def _drain_mailbox_locked(turn: Turn) -> list[str]:
        """Pop every pending mailbox item (caller MUST hold ``turn.lock``)."""
        survivors: list[str] = []
        while True:
            try:
                survivors.append(turn.steering_mailbox.get_nowait())
            except asyncio.QueueEmpty:
                break
        return survivors

    async def requeue_steers_as_new(self, request_id: str, texts: list[str]) -> None:
        """REACT-6/F033 — re-route steers that a stop swallowed as queued-new turns.

        The cooperative-stop callback drains the steering mailbox at the iteration
        boundary and cannot fold the drained steers into a stopping turn. Those
        items were already removed from the mailbox, so the completion-seam
        ``finalize_and_drain`` would find nothing to re-route — the user's
        co-arriving message would be lost. The execute finalize seam hands them
        here so they are re-enqueued as queued-new turns via the SAME shared
        ``_reroute_survivors_locked`` path survivors take. Fail-safe: an unknown
        request id (already deregistered) or an empty list is a no-op; a full
        intake queue is logged-not-raised inside the shared helper.

        Runs under the turn's ``lock`` (the SAME lock ``try_steer`` /
        ``finalize_and_drain`` take) so the intake-deque mutation is serialized
        with any concurrent steer routing on this session.
        """
        if not texts:
            return
        turn = self._turns.get(request_id)
        if turn is None:
            log.gateway.warning(
                "[turn] requeue_steers_as_new: no live turn — steers dropped",
                extra={"_fields": {"request_id": request_id, "count": len(texts)}},
            )
            return
        async with turn.lock:
            self._reroute_survivors_locked(turn, texts)

    async def finalize_and_drain(self, request_id: str) -> list[str]:
        """Atomically FINALIZE the turn then drain+re-route survivors (one lock).

        §9 invariant 1 (lost-steer guard) — the COMPLETION-SEAM side. The
        orchestrator calls this when a turn's provider loop has ENDED but BEFORE
        ``deregister``. Without it, a steer landing in that window (loop done,
        status still RUNNING, not yet deregistered) is ``put`` onto a mailbox whose
        loop will never fold it and is then GC'd — a silently lost instruction.

        Under the turn's per-turn ``lock`` (the SAME lock ``try_steer`` takes, so
        the two are serialized), in ONE atomic critical section:

          1. CAS ``RUNNING→FINALIZING`` — so a CONCURRENT ``try_steer`` that
             acquires the lock AFTER this now reads FINALIZING and converts its
             steer to a queued-new turn (never onto the dead mailbox). An
             already-FINALIZING turn is left as-is (idempotent; never regressed to
             RUNNING, never an illegal transition).
          2. Drain any mailbox survivors (a steer that ``try_steer`` accepted onto
             the RUNNING turn just before this acquired the lock) and re-route each
             as a queued-new turn via the shared ``_reroute_survivors_locked``.

        The flip and the drain are ATOMIC under one lock — there is no instant
        where status reads RUNNING but the drain already ran (which would re-open
        the lost-steer hole). Returns the drained survivor texts (already
        re-enqueued) so the caller can log/act on them; an unknown ``request_id``
        (already deregistered) → ``[]`` (fail-safe, never raises).
        """
        turn = self._turns.get(request_id)
        if turn is None:
            return []
        async with turn.lock:
            # 1. Flip RUNNING->FINALIZING so a steer that acquires the lock AFTER
            #    us converts to queued-new instead of landing on the dead mailbox.
            if turn.status is TurnStatus.RUNNING:
                turn.status = TurnStatus.FINALIZING
            # 2. Drain + re-route any steer accepted onto the (then-RUNNING) turn
            #    before we took the lock — same atomic section, no window.
            survivors = self._drain_mailbox_locked(turn)
            self._reroute_survivors_locked(turn, survivors)
            log.gateway.debug(
                "[turn] finalize_and_drain: finalized + drained survivors",
                extra={"_fields": {
                    "request_id": request_id, "session_id": turn.session_id,
                    "status": turn.status.value, "survivors": len(survivors),
                }},
            )
        return survivors

    async def cas_status(self, request_id: str, expect: TurnStatus, new: TurnStatus) -> bool:
        turn = self._turns.get(request_id)
        if turn is None:
            return False
        async with turn.lock:
            if turn.status is not expect or _NEXT.get(expect) is not new:
                return False
            turn.status = new
            return True

    def enqueue(
        self,
        session_id: str,
        *,
        original_input: str,
        request_id: str,
        target: int | str | None,
    ) -> None:
        q = self._queues.setdefault(session_id, deque())
        if len(q) >= self._per_session_queue_max:
            # Loud overflow: reject-with-notice (orchestrator catches QueueFull and
            # tells the user). Never silently grow the queue unbounded.
            log.gateway.warning(
                "[turn] per-session intake queue full — rejecting",
                extra={
                    "_fields": {
                        "session_id": session_id,
                        "request_id": request_id,
                        "queue_depth": len(q),
                        "per_session_queue_max": self._per_session_queue_max,
                    }
                },
            )
            raise QueueFull(
                f"session {session_id} intake queue full "
                f"({len(q)}/{self._per_session_queue_max})"
            )
        q.append(
            PendingIntake(request_id=request_id, original_input=original_input, target=target)
        )

    def pop_next(self, session_id: str) -> PendingIntake | None:
        q = self._queues.get(session_id)
        if not q:
            return None
        return q.popleft()

    def idle_queued_session(self) -> str | None:
        """Return one session that has queued intakes but NO running turn, if any.

        This is the global-cap-WAKE seam: a turn HELD because the host was at the
        global running cap is enqueued on its own (idle) session, so the
        per-session completion->drain hook never fires for it (that session has no
        running turn to complete). When ANY turn finishes and global capacity
        frees, the orchestrator's drain consults this to surface such a stranded
        session and dispatch its head intake. Deterministic FIFO-ish: first idle
        session (insertion order of the queues dict) with a non-empty queue.
        Returns None when no session is in that state (the common case).
        """
        for sid, q in self._queues.items():
            if q and self._running.get(sid) is None:
                return sid
        return None

    async def deregister(self, request_id: str) -> None:
        turn = self._turns.pop(request_id, None)
        if turn is None:
            return
        if self._running.get(turn.session_id) == request_id:
            self._running.pop(turn.session_id, None)
        log.gateway.debug(
            "[turn] deregister",
            extra={"_fields": {"request_id": request_id}},
        )

    @staticmethod
    def _wedge_redispatch_generation(request_id: str) -> int:
        """How many wedge-driven re-dispatches this request_id already carries.

        The re-dispatch lineage is encoded in the request_id suffix
        (``{base}-redispatch-N``) so no extra ``Turn`` field / ``register`` arg is
        needed (keeps the bound contained to the sweep). A request_id with no such
        suffix is generation 0.
        """
        _base, sep, tail = request_id.rpartition(_REDISPATCH_SUFFIX)
        if not sep:
            return 0
        try:
            return int(tail)
        except ValueError:
            return 0

    def _redispatch_wedged_goal(self, turn: Turn) -> None:
        """F-67 — re-enqueue a reaped WEDGED turn's own goal as a queued-new intake.

        Reuses the existing ``enqueue`` path (the same FIFO queue ``pop_next`` /
        the orchestrator's drain consume), so the next drain re-dispatches the
        wedged turn's ``original_input`` instead of it being silently discarded.
        Bounded: after ``_MAX_WEDGE_REDISPATCH`` wedge re-dispatches the lineage is
        given up (logged, never re-enqueued) so a genuinely-poisonous turn cannot
        loop forever. A full intake queue is loud-but-non-fatal (logged via the
        ``QueueFull`` guard), never raised into the sweep.
        """
        gen = self._wedge_redispatch_generation(turn.turn_id)
        if gen >= _MAX_WEDGE_REDISPATCH:
            log.gateway.error(
                "[turn] wedged turn exhausted re-dispatch budget — goal given up",
                extra={"_fields": {
                    "request_id": turn.turn_id, "session_id": turn.session_id,
                    "generation": gen, "max": _MAX_WEDGE_REDISPATCH,
                }},
            )
            return
        new_rid = f"{turn.turn_id}{_REDISPATCH_SUFFIX}{gen + 1}"
        try:
            self.enqueue(
                turn.session_id,
                original_input=turn.original_input,
                request_id=new_rid,
                target=turn.target,
            )
            log.gateway.warning(
                "[turn] wedged turn re-dispatched — goal not lost (F-67)",
                extra={"_fields": {
                    "request_id": turn.turn_id, "new_request_id": new_rid,
                    "session_id": turn.session_id, "generation": gen + 1,
                }},
            )
        except QueueFull as exc:
            # Intake queue full — cannot re-dispatch. Loud, never silent: the goal
            # is dropped here but logged as lost (the alternative — unbounded queue
            # growth — is worse). Never raised into the scheduler sweep.
            log.gateway.error(
                "[turn] wedged turn re-dispatch failed — intake queue full, goal DROPPED",
                exc_info=exc,
                extra={"_fields": {
                    "request_id": turn.turn_id, "session_id": turn.session_id,
                }},
            )

    async def sweep(self, *, ttl_seconds: float) -> list[str]:
        """Backstop: reap a turn whose TASK IS DONE but status never reached DONE.

        F050: the reap predicate is gated behind ``task.done()`` — the expired-TTL
        clause only tightens *when* a done-stuck turn is reaped, it MUST NEVER reap
        a still-RUNNING (``not task.done()``) turn. Reaping a live turn would free
        its ``_running`` slot and let a concurrent same-session message start a
        SECOND running turn (two writers to one chat history — the race
        ``project_concurrent_message_handling`` deleted by construction).

        Snapshot keys THEN act — never iterate-and-mutate (dict changed size). After
        a reap that freed a ``_running`` slot, surface stranded sessions to the
        drain seam (self-healing: a failing drainer is logged, never raised).
        """
        now = time.monotonic()
        reaped: list[str] = []
        freed_running = False
        for rid in list(self._turns.keys()):  # snapshot
            turn = self._turns.get(rid)
            if turn is None:
                continue
            done = turn.task is not None and turn.task.done()
            expired = (now - turn.started_at) >= ttl_seconds
            # done AND not-yet-DONE → the wedge; expired only ever NARROWS the
            # done-set, never reaps a not-done turn (the bare-expired foot-gun).
            if done and (turn.status is not TurnStatus.DONE or expired):
                was_running = self._running.get(turn.session_id) == rid
                wedged = turn.status is not TurnStatus.DONE
                await self.deregister(rid)
                if was_running:
                    freed_running = True
                reaped.append(rid)
                log.gateway.warning(
                    "[turn] sweeper reaped",
                    extra={"_fields": {
                        "request_id": rid,
                        "session_id": turn.session_id,
                        "reason": "done_not_DONE" if wedged else "expired_done",
                    }},
                )
                # F-67 — a WEDGED turn that held the running slot had its goal
                # actively being worked when it stalled; reaping alone discards
                # ``original_input`` and the user must re-ask. Re-enqueue its OWN
                # goal as a fresh queued-new intake (the same path drain consumes),
                # so the next drain re-dispatches it. Bounded by the re-dispatch
                # lineage so a poisonous turn that keeps wedging cannot loop. NOT
                # done for a turn that reached DONE (it completed) nor for a reaped
                # non-running survivor key (it owns no live goal).
                if was_running and wedged:
                    self._redispatch_wedged_goal(turn)
        # Stranded-session drain: a reaped session may have a queued intake and no
        # running turn — only ANOTHER turn finishing would otherwise wake it (silent
        # unresponsiveness). Surface it to the global-cap drain seam.
        if freed_running and self._on_stranded is not None:
            try:
                await self._on_stranded()
            except Exception as exc:  # B5 — never crash the scheduler loop
                log.gateway.error(
                    "[turn] sweeper stranded-drain failed — continuing", exc_info=exc
                )
        # STEER-3/F057 — reclaim parked raw IngressMessages for the reaped turns
        # (a wedged/GC'd turn never drains its parked entry → a slow leak). Fired
        # for EVERY reap (not just freed-running ones — a reaped survivor key has
        # no _running slot but still leaks). Self-healing: a failing evictor is
        # logged, never raised into the scheduler loop.
        if reaped and self._on_reaped is not None:
            try:
                self._on_reaped(reaped)
            except Exception as exc:  # B5 — never crash the scheduler loop
                log.gateway.error(
                    "[turn] sweeper parked-evict failed — continuing", exc_info=exc
                )
        return reaped
