from __future__ import annotations

import asyncio
import enum
import time
from collections import deque
from dataclasses import dataclass, field

from stackowl.infra.observability import log

_MAILBOX_MAX = 8


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

    def __init__(self) -> None:
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
        self._queues.setdefault(session_id, deque()).append(
            PendingIntake(request_id=request_id, original_input=original_input, target=target)
        )

    def pop_next(self, session_id: str) -> PendingIntake | None:
        q = self._queues.get(session_id)
        if not q:
            return None
        return q.popleft()

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
