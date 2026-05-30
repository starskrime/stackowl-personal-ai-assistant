"""ClarifyGateway — in-memory pending-clarify registry + per-channel delivery.

Two pause modes share one registry:

* **Blocking-await (PRIMARY).** :meth:`ask` is called with ``blocking=True``; an
  :class:`asyncio.Event` is created on the entry BEFORE delivery and the tool
  parks on :meth:`wait_for_answer` mid-turn. When the user's reply arrives the
  gateway loop calls :meth:`try_resolve`, which writes the answer onto the entry
  and SETS its event — waking the parked waiter IN THE SAME TURN. The concurrent
  gateway loop (send decoupled from receive) is what frees the loop while a tool
  is parked.
* **Turn-yield (FALLBACK).** :meth:`ask` with ``blocking=False`` (the default)
  records a :class:`PendingClarify` with NO event and the turn ends — no
  coroutine is parked. The gateway loop calls :meth:`try_resolve` on the next
  inbound message; the matching entry is popped and that message becomes the
  answer to a fresh resume turn.

:meth:`try_resolve` matches the first non-expired entry for BOTH ``session_id``
AND ``channel``. Pop ownership is split by mode so resolution is
ORDER-INDEPENDENT (resolve-before-park AND park-before-resolve both deliver the
answer): a TURN-YIELD entry (no event) is popped by ``try_resolve``; a BLOCKING
entry (event present) is NOT popped by ``try_resolve`` — it only writes the
answer + sets the event and leaves the entry in ``_pending`` so a not-yet-parked
:meth:`wait_for_answer` can still find it by id. For blocking entries the WAITER
owns the pop. The router distinguishes the two modes by inspecting the returned
entry's ``event``: a non-``None`` event that is now *set* means a blocking
waiter was/will be woken in-turn (the router must NOT start a fresh turn); a
``None`` event means a turn-yield resume.

Invariants (party-mode E5 review — INC-1..4, B1/B2/B3, Security):

* **Cap one pending per session.** A new ``ask`` for a session that already has
  a pending entry REPLACES it (logged), never accumulates. Bounds per-session
  state and matches the single-pending-clarify MVS rule.
* **session+channel binding is enforced inside ``try_resolve``** — never
  bypassable at a higher layer. A mismatched channel or session returns ``None``
  and leaves the entry intact (prevents the cross-session answer leak, INC-3).
* **TTL-bounded.** :meth:`sweep_expired` drops entries older than a TTL so a
  much-later unrelated message is not mis-resolved as an answer.
* **Self-healing.** No public method raises — delivery failures, missing
  adapters, and unexpected errors degrade to a logged no-op/false return so the
  pipeline keeps running.

Clock-injectable (``time_fn``) so TTL/expiry tests do not sleep. Provenance:
HYBRID port (algorithm only) — see the package docstring.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.channels.base import ChannelAdapter

# Entropy for clarify ids — token_urlsafe(_ID_BYTES) yields a non-sequential,
# collision-resistant id (Security: never sequential, INC-3).
_ID_BYTES = 16


@dataclass(slots=True)
class PendingClarify:
    """One outstanding clarify question awaiting the user's next message.

    The id is minted by the gateway; ``created_at`` is a monotonic-ish timestamp
    from the gateway's injected clock, used only for TTL expiry comparison.

    ``event`` is ``None`` for a turn-yield entry (no coroutine parked) and an
    :class:`asyncio.Event` for a blocking entry (a waiter is/was parked on
    :meth:`ClarifyGateway.wait_for_answer`). ``answer`` is written by
    :meth:`ClarifyGateway.try_resolve` just before the event is set, so the woken
    waiter can read it. Both are mutable so resolution can mutate the live entry.
    """

    clarify_id: str
    session_id: str
    channel: str
    question: str
    choices: tuple[str, ...] = ()
    awaiting_text: bool = False
    created_at: float = 0.0
    answer: str | None = None
    event: asyncio.Event | None = None


@dataclass
class ClarifyGateway:
    """DI singleton: pending-clarify registry + per-channel delivery adapters.

    Constructed once and registered on :class:`StepServices`. The startup layer
    calls :meth:`register_adapter` for each live channel; tools call
    :meth:`ask`; the gateway loop calls :meth:`try_resolve` / :meth:`sweep_expired`;
    session lifecycle (``/new``, shutdown, cached-agent eviction) calls
    :meth:`clear_session`.
    """

    # Clock is injectable for deterministic TTL tests; defaults to monotonic so
    # entries are never mis-aged by wall-clock jumps.
    time_fn: Callable[[], float] = time.monotonic
    _pending: dict[str, PendingClarify] = field(default_factory=dict)
    _adapters: dict[str, ChannelAdapter] = field(default_factory=dict)

    # ----------------------------------------------------------- adapter wiring

    def register_adapter(self, channel: str, adapter: ChannelAdapter) -> None:
        """Register the delivery adapter for ``channel`` (idempotent overwrite)."""
        self._adapters[channel] = adapter
        log.gateway.debug(
            "clarify_gateway.register_adapter: registered",
            extra={"_fields": {"channel": channel}},
        )

    # --------------------------------------------------------------------- ask

    async def ask(
        self,
        session_id: str,
        channel: str,
        question: str,
        *,
        choices: tuple[str, ...] = (),
        awaiting_text: bool = False,
        blocking: bool = False,
    ) -> str:
        """Register a pending clarify for ``session_id`` and deliver it.

        Mints a non-sequential ``clarify_id``, stores the entry (CAP ONE per
        session — an existing pending entry is REPLACED, never accumulated), and
        delivers via the channel's registered adapter's ``send_clarify``. If no
        adapter is registered for ``channel`` the entry is STILL stored (so a
        later :meth:`try_resolve` works) but delivery is logged as failed.

        When ``blocking`` is ``True`` an :class:`asyncio.Event` is created on the
        entry BEFORE delivery (inside this running-loop coroutine, so it binds to
        the active loop); the caller then parks on :meth:`wait_for_answer`. When
        ``blocking`` is ``False`` (default) the entry has no event — turn-yield.

        If the prior pending entry being replaced had a still-unset parked waiter
        (its event exists and is not set), that orphaned event is SET first (with
        ``answer=None``) so the old waiter unblocks instead of leaking forever.

        Returns the ``clarify_id``. Never raises — a delivery error is logged and
        swallowed (the question is still registered, so the user's reply can
        still resolve it once delivery is retried by a higher layer).
        """
        clarify_id = secrets.token_urlsafe(_ID_BYTES)
        # 1. ENTRY
        log.gateway.info(
            "clarify_gateway.ask: entry",
            extra={
                "_fields": {
                    "session_id": session_id,
                    "channel": channel,
                    "clarify_id": clarify_id,
                    "n_choices": len(choices),
                    "awaiting_text": awaiting_text,
                    "blocking": blocking,
                }
            },
        )

        # CAP ONE per session: drop any prior pending entry for this session so we
        # never accumulate (party Security INC-2; bounds per-session state). Any
        # parked waiter on a replaced blocking entry is woken (timed_out) first so
        # it cannot leak forever.
        replaced = [
            cid for cid, e in self._pending.items() if e.session_id == session_id
        ]
        for cid in replaced:
            prior = self._pending.pop(cid, None)
            if prior is not None:
                self._abandon_waiter(prior, reason="superseded")
        if replaced:
            log.gateway.info(
                "clarify_gateway.ask: replacing prior pending clarify (cap=1/session)",
                extra={"_fields": {"session_id": session_id, "replaced": replaced}},
            )

        # Create the Event inside this coroutine so it binds to the running loop.
        event = asyncio.Event() if blocking else None

        self._pending[clarify_id] = PendingClarify(
            clarify_id=clarify_id,
            session_id=session_id,
            channel=channel,
            question=question,
            choices=tuple(choices),
            awaiting_text=awaiting_text,
            created_at=self.time_fn(),
            event=event,
        )

        # 3. STEP — deliver via the channel's adapter (self-healing on failure).
        adapter = self._adapters.get(channel)
        if adapter is None:
            log.gateway.warning(
                "clarify_gateway.ask: no adapter registered — registered but undelivered",
                extra={"_fields": {"channel": channel, "clarify_id": clarify_id}},
            )
        else:
            try:
                await adapter.send_clarify(session_id, question, choices, clarify_id)
            except Exception as exc:  # self-healing — delivery must not crash the turn
                log.gateway.error(
                    "clarify_gateway.ask: delivery failed — entry kept",
                    exc_info=exc,
                    extra={"_fields": {"channel": channel, "clarify_id": clarify_id}},
                )

        # 4. EXIT
        log.gateway.info(
            "clarify_gateway.ask: exit",
            extra={
                "_fields": {
                    "clarify_id": clarify_id,
                    "delivered": adapter is not None,
                    "blocking": blocking,
                }
            },
        )
        return clarify_id

    # --------------------------------------------------------- wait_for_answer

    async def wait_for_answer(
        self, clarify_id: str, timeout: float,
    ) -> tuple[str | None, bool]:
        """Park on the blocking entry's event until resolved, timed out, or gone.

        The WAITER owns the pop (resolve-before-park safe — see :meth:`try_resolve`).
        The entry is captured by id ONCE up front and the reference is held for the
        rest of the call; the answer is read from that held reference, never re-got
        by id (the entry may already be gone from ``_pending``).

        Returns ``(answer, timed_out)``:

        * Entry absent at entry (already resolved+popped / expired / cleared /
          never existed) → ``(None, True)``.
        * Event already set when we arrive (resolve-before-park) → skip waiting,
          pop, and return ``(answer, False)``.
        * Event set by :meth:`try_resolve` within ``timeout`` → pop, ``(answer, False)``.
        * Event set WITHOUT an answer (the entry was abandoned by a cap-one
          replace / clear_session / sweep_expired) → pop, ``(None, True)`` (timed_out).
        * ``timeout`` elapsed first → the entry is POPPED (so a late reply is
          ignored, never mis-resolved) and ``(None, True)`` is returned.

        Self-healing: any unexpected error pops the entry and returns
        ``(None, True)``. ``CancelledError`` propagates (cooperative cancellation).
        Only call this for a ``blocking=True`` entry whose ``event`` exists; an
        entry with no event yields ``(None, True)``.
        """
        # Capture the entry by id ONCE here and hold the reference for the rest of
        # the call. The WAITER owns the pop (try_resolve no longer pops a blocking
        # entry — see its docstring), which makes resolution order-independent: a
        # resolve that lands BEFORE this coroutine parks has already set the event
        # AND left the entry in _pending, so we still find it here and read its
        # answer; a resolve that lands AFTER we park wakes us via the held event.
        entry = self._pending.get(clarify_id)
        if entry is None or entry.event is None:
            log.gateway.debug(
                "clarify_gateway.wait_for_answer: no parked entry",
                extra={"_fields": {"clarify_id": clarify_id, "present": entry is not None}},
            )
            return (None, True)
        try:
            if not entry.event.is_set():
                # Not yet resolved → park. (If already set — resolve-before-park —
                # skip the wait entirely and read the answer below.)
                await asyncio.wait_for(entry.event.wait(), timeout)
        except TimeoutError:  # asyncio.TimeoutError aliases the builtin (3.11+)
            self._pending.pop(clarify_id, None)
            log.gateway.info(
                "clarify_gateway.wait_for_answer: timed out — entry popped",
                extra={"_fields": {"clarify_id": clarify_id, "timeout": timeout}},
            )
            return (None, True)
        except asyncio.CancelledError:  # cooperative cancellation must propagate
            # Pop the parked entry FIRST so a cancelled wait (shutdown teardown /
            # cancelled send-task) does not leak a ghost in _pending. A later
            # try_resolve would otherwise match the ghost, set a dead event, and
            # silently drop the real answer. CancelledError is a BaseException
            # (3.8+) so the generic `except Exception` below never catches it —
            # the pop must be explicit here. Then RE-RAISE (never swallow).
            self._pending.pop(clarify_id, None)
            log.gateway.info(
                "clarify_gateway.wait_for_answer: cancelled — entry popped, re-raising",
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            raise
        except Exception as exc:  # self-healing — never raise into the parked tool
            self._pending.pop(clarify_id, None)
            log.gateway.error(
                "clarify_gateway.wait_for_answer: failed — treating as timeout",
                exc_info=exc,
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            return (None, True)
        # Resolved (or already-resolved). The WAITER pops the entry now — idempotent
        # if try_resolve/abandon already removed it. Read the answer from the HELD
        # reference (never re-get by id — the entry may already be gone from the map).
        #
        # A real resolve writes a (non-None) answer before setting the event; an
        # abandonment (cap-one replace / clear_session / sweep_expired) sets the
        # event with answer still None. The latter is reported as timed_out so the
        # parked waiter degrades gracefully instead of treating None as an answer.
        self._pending.pop(clarify_id, None)
        if entry.answer is None:
            log.gateway.info(
                "clarify_gateway.wait_for_answer: woken without answer (abandoned)",
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            return (None, True)
        log.gateway.info(
            "clarify_gateway.wait_for_answer: woken with answer",
            extra={"_fields": {"clarify_id": clarify_id}},
        )
        return (entry.answer, False)

    # ------------------------------------------------------------- try_resolve

    def try_resolve(
        self, session_id: str, channel: str, answer: str,  # noqa: ARG002 — answer is the caller's, returned via entry
    ) -> PendingClarify | None:
        """Resolve the pending clarify matching ``session_id`` AND ``channel``.

        Returns the matched :class:`PendingClarify` on a match, or ``None`` if
        there is no non-expired entry for this exact session+channel pair.

        Pop ownership is split by mode so resolution is ORDER-INDEPENDENT:

        * **BLOCKING entry** (``match.event is not None``): write the answer onto
          the entry and SET its event — but do NOT pop it. The entry stays in
          ``_pending`` so a :meth:`wait_for_answer` that has not parked yet
          (resolve-before-park) can still find it by id and read the answer; a
          waiter that is already parked is woken by the set event. The WAITER
          owns the pop. The set event remains the gateway-loop router's signal
          that this was a blocking (in-turn) resolve.
        * **TURN-YIELD entry** (``match.event is None``): POP and return, exactly
          as before — there is no parked waiter, so the next-message resume folds
          the popped entry's question + reply into a fresh turn.

        Both keys are enforced HERE and are never bypassable (Security INC-3): a
        mismatched channel or session leaves the entry intact and returns
        ``None``.

        Idempotency: for a turn-yield resolve a second call returns ``None`` (the
        entry was popped). For a blocking resolve the entry lingers until the
        waiter pops it; a SECOND reply in that tiny window re-matches the same
        entry and re-sets it with the new answer (benign — the event is already
        set, the waiter is already woken and reads whichever answer is current).
        Once the waiter pops, a further call returns ``None``. Never raises.
        """
        try:
            match = next(
                (
                    e
                    for e in self._pending.values()
                    if e.session_id == session_id and e.channel == channel
                ),
                None,
            )
            if match is None:
                log.gateway.debug(
                    "clarify_gateway.try_resolve: no match",
                    extra={"_fields": {"session_id": session_id, "channel": channel}},
                )
                return None
            blocking = self._resolve_entry(match, answer)
            log.gateway.info(
                "clarify_gateway.try_resolve: resolved",
                extra={
                    "_fields": {
                        "session_id": session_id,
                        "channel": channel,
                        "clarify_id": match.clarify_id,
                        "answer_len": len(answer),
                        "blocking": blocking,
                    }
                },
            )
            return match
        except Exception as exc:  # self-healing — never raise into the gateway loop
            log.gateway.error(
                "clarify_gateway.try_resolve: failed — treating as no match",
                exc_info=exc,
                extra={"_fields": {"session_id": session_id, "channel": channel}},
            )
            return None

    # ------------------------------------------------------- try_resolve_by_id

    def try_resolve_by_id(
        self, clarify_id: str, answer: str,
    ) -> PendingClarify | None:
        """Resolve the SPECIFIC pending clarify identified by ``clarify_id``.

        Identical resolution semantics to :meth:`try_resolve`'s by-(session,
        channel) path, but keyed on the disambiguating ``clarify_id`` the tap
        CARRIES rather than a session+channel re-match. This matters once the
        cap-one-per-session MVS rule is relaxed: a session could then hold
        multiple pending entries, and a session+channel match would resolve
        whichever entry comes first — possibly NOT the one the user tapped. The
        id is exact, so the tapped question is the one resolved.

        Returns the matched :class:`PendingClarify`, or ``None`` if no entry
        exists for ``clarify_id`` (already answered, superseded, expired,
        cleared, or never minted).

        Pop ownership is split by mode exactly as in :meth:`try_resolve`:

        * **BLOCKING entry** (``event is not None``): write the answer + SET the
          event; do NOT pop (the WAITER owns the pop — resolve-before-park safe).
        * **TURN-YIELD entry** (``event is None``): POP and return.

        Self-healing — never raises (any unexpected error logs and returns
        ``None``). :meth:`try_resolve` keeps its session+channel signature
        because a TYPED reply carries no id and must still match by binding.
        """
        try:
            match = self._pending.get(clarify_id)
            if match is None:
                log.gateway.debug(
                    "clarify_gateway.try_resolve_by_id: no match",
                    extra={"_fields": {"clarify_id": clarify_id}},
                )
                return None
            blocking = self._resolve_entry(match, answer)
            log.gateway.info(
                "clarify_gateway.try_resolve_by_id: resolved",
                extra={
                    "_fields": {
                        "clarify_id": clarify_id,
                        "answer_len": len(answer),
                        "blocking": blocking,
                    }
                },
            )
            return match
        except Exception as exc:  # self-healing — never raise into the callback path
            log.gateway.error(
                "clarify_gateway.try_resolve_by_id: failed — treating as no match",
                exc_info=exc,
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            return None

    # ----------------------------------------------------------- _resolve_entry

    def _resolve_entry(self, entry: PendingClarify, answer: str) -> bool:
        """Apply the mode-split resolution to a matched entry; return ``blocking``.

        Shared body of :meth:`try_resolve` and :meth:`try_resolve_by_id` so both
        deliver IDENTICAL semantics:

        * **BLOCKING entry** (``entry.event is not None``): hand the answer to the
          (possibly not-yet-parked) waiter and wake it by setting the event. Do
          NOT pop — the WAITER owns the pop so a resolve-before-park reply is
          never discarded. The set event is both the wake signal and the gateway
          loop router's blocking-resolve marker.
        * **TURN-YIELD entry** (``entry.event is None``): no parked waiter — pop
          here.

        Returns ``True`` for a blocking entry, ``False`` for a turn-yield entry.
        """
        blocking = entry.event is not None
        if entry.event is not None:
            entry.answer = answer
            entry.event.set()
        else:
            self._pending.pop(entry.clarify_id, None)
        return blocking

    # --------------------------------------------------------------------- peek

    def peek(self, clarify_id: str) -> PendingClarify | None:
        """Read-only lookup of the pending entry by ``clarify_id``.

        Returns the live :class:`PendingClarify` for ``clarify_id`` or ``None``
        if no such entry exists (already answered, superseded, expired, cleared,
        or never minted). NEVER pops the entry and NEVER touches its event —
        unlike :meth:`try_resolve` this is a pure read. Used by the inline-button
        callback resolver to map a tapped button index → the choice text + the
        entry's ``session_id``/``channel``, and to detect a stale/superseded tap
        (``None``). Never raises.
        """
        try:
            entry = self._pending.get(clarify_id)
            log.gateway.debug(
                "clarify_gateway.peek: lookup",
                extra={"_fields": {"clarify_id": clarify_id, "found": entry is not None}},
            )
            return entry
        except Exception as exc:  # self-healing — a read must never raise
            log.gateway.error(
                "clarify_gateway.peek: failed — treating as not found",
                exc_info=exc,
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            return None

    # --------------------------------------------------------- _abandon_waiter

    @staticmethod
    def _abandon_waiter(entry: PendingClarify, *, reason: str) -> None:
        """Wake a parked blocking waiter without an answer so it cannot leak.

        If ``entry`` has an event that is still unset, set it (leaving
        ``answer=None``) so the parked :meth:`wait_for_answer` unblocks and
        returns ``(None, True)`` (timed_out) instead of hanging forever. A no-op
        for turn-yield entries (no event) and for already-set events. Never raises.
        """
        ev = entry.event
        if ev is not None and not ev.is_set():
            entry.answer = None
            ev.set()
            log.gateway.info(
                "clarify_gateway._abandon_waiter: woke parked waiter (no answer)",
                extra={"_fields": {"clarify_id": entry.clarify_id, "reason": reason}},
            )

    # ------------------------------------------------------------ clear_session

    def clear_session(self, session_id: str) -> list[str]:
        """Drop all pending entries for ``session_id``; return their ids.

        Wired into ``/new``, shutdown, and cached-agent eviction so an abandoned
        clarify never lingers (party Operations). Any parked blocking waiter is
        woken (timed_out) first so it cannot leak. Never raises.
        """
        try:
            dropped = [
                cid for cid, e in self._pending.items() if e.session_id == session_id
            ]
            for cid in dropped:
                entry = self._pending.pop(cid, None)
                if entry is not None:
                    self._abandon_waiter(entry, reason="clear_session")
            if dropped:
                log.gateway.info(
                    "clarify_gateway.clear_session: dropped pending clarifies",
                    extra={"_fields": {"session_id": session_id, "dropped": dropped}},
                )
            return dropped
        except Exception as exc:  # self-healing
            log.gateway.error(
                "clarify_gateway.clear_session: failed",
                exc_info=exc,
                extra={"_fields": {"session_id": session_id}},
            )
            return []

    # ------------------------------------------------------------ sweep_expired

    def sweep_expired(self, ttl_seconds: float) -> int:
        """Drop entries older than ``ttl_seconds``; return the count dropped.

        Bounds the "next message is the answer" window (prevents a much-later
        unrelated message resolving a stale clarify). Any parked blocking waiter
        on an expired entry is woken (timed_out) first so it cannot leak. Never
        raises.
        """
        try:
            now = self.time_fn()
            expired = [
                cid
                for cid, e in self._pending.items()
                if now - e.created_at >= ttl_seconds
            ]
            for cid in expired:
                entry = self._pending.pop(cid, None)
                if entry is not None:
                    self._abandon_waiter(entry, reason="sweep_expired")
            if expired:
                log.gateway.info(
                    "clarify_gateway.sweep_expired: dropped expired clarifies",
                    extra={"_fields": {"ttl_seconds": ttl_seconds, "expired": expired}},
                )
            return len(expired)
        except Exception as exc:  # self-healing
            log.gateway.error(
                "clarify_gateway.sweep_expired: failed",
                exc_info=exc,
                extra={"_fields": {"ttl_seconds": ttl_seconds}},
            )
            return 0
