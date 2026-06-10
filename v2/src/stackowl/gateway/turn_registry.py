from __future__ import annotations

import asyncio
import enum
import os
import time
from collections import deque
from dataclasses import dataclass, field

from stackowl.infra.observability import log

_MAILBOX_MAX = 8

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
    target: int | None


@dataclass
class Turn:
    turn_id: str  # == request_id
    session_id: str
    task: asyncio.Task[None] | None
    target: int | None
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

    @property
    def per_session_queue_max(self) -> int:
        return self._per_session_queue_max

    @property
    def global_running_max(self) -> int:
        return self._global_running_max

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

    def running(self, session_id: str) -> Turn | None:
        rid = self._running.get(session_id)
        return self._turns.get(rid) if rid else None

    async def register(
        self,
        request_id: str,
        *,
        session_id: str,
        task: asyncio.Task[None] | None,
        target: int | None,
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
        target: int | None,
    ) -> str:
        """Atomically route a steer to a RUNNING turn, or convert it to a new turn.

        §9 invariant 1 (lost-steer guard) — the enqueue side. A steer must NEVER
        land in a dead turn's mailbox. Under the turn's per-turn ``lock`` (so the
        status-read and the put are ONE atomic critical section vs.
        ``finalize_if_drained`` which holds the same lock):

          * status ``RUNNING`` → ``steering_mailbox.put_nowait(text)`` and return
            ``"STEER"`` (the live loop will fold it at its next iteration boundary).
          * status ``FINALIZING``/``DONE`` (turn is past its finalization line) →
            ``enqueue`` the text as a queued-new turn and return ``"NEW"`` (the
            caller dispatches it as a fresh turn — never onto the dead mailbox).

        Fail-safe: an unknown ``request_id`` (already deregistered) is treated as
        past-finalization → convert to a queued-new turn, return ``"NEW"`` (a
        discarded steer is a lost instruction, never silently dropped).

        On a full mailbox the steer is converted to a queued-new turn instead of
        being dropped (loud ``QueueFull`` from ``enqueue`` propagates to the caller
        which already handles reject-with-notice).
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
                try:
                    turn.steering_mailbox.put_nowait(text)
                except asyncio.QueueFull:
                    # Mailbox saturated — do NOT drop the steer. Convert to a
                    # queued-new turn so the instruction is never lost.
                    log.gateway.warning(
                        "[turn] try_steer: mailbox full — converting steer to queued-new",
                        extra={"_fields": {
                            "request_id": request_id, "request_id_new": request_id_new,
                            "session_id": session_id,
                        }},
                    )
                    self.enqueue(
                        session_id, original_input=text,
                        request_id=request_id_new, target=target,
                    )
                    return "NEW"
                log.gateway.debug(
                    "[turn] try_steer: accepted by RUNNING turn",
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

    async def finalize_if_drained(self, request_id: str) -> bool:
        """Re-check the mailbox under lock, then CAS RUNNING→FINALIZING if empty.

        §9 invariant 1 (lost-steer guard) — the finalize side. Under the SAME
        per-turn ``lock`` ``try_steer`` takes (so a steer arriving concurrently is
        serialized against this check):

          * mailbox non-empty → return ``False`` (a last-moment steer is pending;
            the caller MUST loop again and fold it before finalizing — never go
            FINALIZING with pending steers).
          * mailbox empty → CAS ``RUNNING→FINALIZING`` and return ``True``.

        An unknown ``request_id`` (already deregistered) is already past its
        finalization line → return ``True`` (the caller stops looping; nothing to
        finalize).
        """
        turn = self._turns.get(request_id)
        if turn is None:
            return True
        async with turn.lock:
            if not turn.steering_mailbox.empty():
                log.gateway.debug(
                    "[turn] finalize_if_drained: pending steer — not finalizing",
                    extra={"_fields": {"request_id": request_id}},
                )
                return False
            # Empty under the lock → safe to advance. Inline CAS (we already hold
            # the lock; cas_status would re-acquire it → not re-entrant).
            if turn.status is TurnStatus.RUNNING:
                turn.status = TurnStatus.FINALIZING
            log.gateway.debug(
                "[turn] finalize_if_drained: drained — FINALIZING",
                extra={"_fields": {"request_id": request_id, "status": turn.status.value}},
            )
            return True

    async def drain_survivors(self, request_id: str) -> list[str]:
        """On teardown, drain remaining mailbox items and re-route each as new.

        §9 invariant 1 (lost-steer guard) — the teardown side. A steer accepted by
        a RUNNING turn but never folded (e.g. it arrived after the loop's last
        iteration boundary) would otherwise be GC'd with the turn — a LOST
        instruction. Drain all remaining items under the lock and ``enqueue`` each
        as a queued-new turn (FIFO, inheriting the turn's ``target``/``session_id``),
        returning the drained texts so the caller can also act on them.

        Each re-routed survivor gets a fresh request id derived from the turn id +
        ordinal (``"{request_id}-survivor-{i}"``) so the orchestrator's queued-new
        dispatch keys them uniquely. An unknown ``request_id`` → ``[]``.
        """
        turn = self._turns.get(request_id)
        if turn is None:
            return []
        survivors: list[str] = []
        async with turn.lock:
            while True:
                try:
                    survivors.append(turn.steering_mailbox.get_nowait())
                except asyncio.QueueEmpty:
                    break
            for i, text in enumerate(survivors):
                try:
                    self.enqueue(
                        turn.session_id,
                        original_input=text,
                        request_id=f"{request_id}-survivor-{i}",
                        target=turn.target,
                    )
                    log.gateway.info(
                        "[turn] drain_survivors: re-routed survivor steer as queued-new",
                        extra={"_fields": {
                            "request_id": request_id, "session_id": turn.session_id,
                            "survivor_index": i,
                        }},
                    )
                except QueueFull as exc:
                    # The intake queue is full — the survivor cannot be re-routed.
                    # Loud, never silent: the steer is dropped but logged as a lost
                    # instruction so it is visible (the alternative — unbounded
                    # queue growth — is worse). Remaining survivors stay in the
                    # returned list so the caller still sees them.
                    log.gateway.error(
                        "[turn] drain_survivors: intake queue full — survivor steer DROPPED",
                        exc_info=exc,
                        extra={"_fields": {
                            "request_id": request_id, "session_id": turn.session_id,
                            "survivor_index": i,
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
        target: int | None,
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

    async def sweep(self, *, ttl_seconds: float) -> list[str]:
        """Backstop: reap turns whose task is done but status not terminal, or past TTL.

        Snapshot keys THEN act — never iterate-and-mutate (dict changed size).
        """
        now = time.monotonic()
        reaped: list[str] = []
        for rid in list(self._turns.keys()):  # snapshot
            turn = self._turns.get(rid)
            if turn is None:
                continue
            done = turn.task is not None and turn.task.done()
            expired = (now - turn.started_at) >= ttl_seconds
            if (done and turn.status is not TurnStatus.DONE) or expired:
                await self.deregister(rid)
                reaped.append(rid)
                log.gateway.warning(
                    "[turn] sweeper reaped",
                    extra={"_fields": {"request_id": rid}},
                )
        return reaped
