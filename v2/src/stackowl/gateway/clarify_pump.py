"""ClarifyPump — the clarify-aware turn dispatch shared by every channel loop.

The gateway message loops (CLI, Telegram, …) used to ``await adapter.send(reader)``
inline, which COUPLES delivery to the receive loop: a turn that PARKS on a
clarify question would deadlock the loop so the user's reply could never arrive.
``ClarifyPump`` breaks that coupling and owns the three concerns a clarify-capable
loop needs:

* :meth:`resolve_or_rewrite` — intercept a reply to a pending clarify BEFORE any
  stream is created. A *blocking* resolve (the parked turn's waiter was woken)
  returns ``consumed=True`` so the loop starts NO new turn; a *turn-yield* resolve
  folds the question + reply into a fresh resume turn; ``/reset`` clears the
  session's pending clarify; everything else passes through untouched.
* :meth:`spawn_send` — drain the response stream in its OWN task so the receive
  loop is free while a turn is parked. It also guards the producer: if the turn's
  producer task crashes (or is cancelled) BEFORE :mod:`deliver` closes the writer,
  the send task would otherwise hang forever and wedge the session — so a producer
  done-callback closes the writer, guaranteeing the send drains and the stream is
  reaped (party-mode review B-1).

The class is deliberately framed around primitives (``session_id``, ``channel``,
``route``, ``target``, ``input_text``) rather than the inbound-message /
route-decision types, so both channel loops share ONE implementation and the
smoke/unit tests exercise the REAL pump instead of a re-simulation.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.interaction.clarify_gateway import ClarifyGateway
    from stackowl.interaction.intent_classifier import ClarifyIntentClassifier
    from stackowl.pipeline.streaming import (
        ResponseChunk,
        StreamRegistry,
        StreamWriter,
    )

# The reset command name whose dispatch should also clear a pending clarify.
_RESET_COMMAND = "reset"


class _SendableAdapter(Protocol):
    """Minimal surface the pump needs from a channel adapter."""

    async def send(self, reader: AsyncIterator[ResponseChunk]) -> None: ...  # noqa: D102


class _ClosableWriter(Protocol):
    async def close(self) -> None: ...  # noqa: D102


class ClarifyPump:
    """Clarify-aware turn dispatch for one channel loop (owns its in-flight map)."""

    def __init__(
        self,
        clarify_gateway: ClarifyGateway,
        stream_registry: StreamRegistry,
        intent_classifier: ClarifyIntentClassifier | None = None,
    ) -> None:
        self._gateway = clarify_gateway
        self._stream_registry = stream_registry
        # Optional: classifies a during-park reply as answer vs new-request so an
        # unrelated message isn't swallowed as the answer. None → always treat a
        # reply as the answer (the pre-D behavior).
        self._classifier = intent_classifier
        # Per-loop in-flight send tasks, keyed by session, so loop teardown
        # (:meth:`drain`) can await every still-delivering turn. The serialize
        # gate that this map once backed (``serialize_prior``) is GONE — same-
        # session ordering is now owned by the TurnRegistry (at most one RUNNING
        # turn per session + a FIFO intake queue), so the map is purely a
        # drain/reap ledger now (§4.3).
        self._inflight: dict[str, asyncio.Task[None]] = {}

    # ----------------------------------------------------------- resolve-router

    async def resolve_or_rewrite(
        self, *, session_id: str, channel: str, route: str, target: str, input_text: str,
    ) -> tuple[bool, str]:
        """Intercept a reply to a pending clarify.

        Returns ``(consumed, input_text)``:

        * ``consumed=True`` — a BLOCKING parked turn was just resumed in-place
          (its waiter was woken); the loop must start NO new turn.
        * ``consumed=False`` — no pending clarify, a new-request that gracefully
          cancelled the pending clarify (run ``input_text`` as a fresh turn), a
          turn-yield resolve folded into the returned ``input_text``, or a normal
          message.

        When a clarify is pending and an intent classifier is configured, the
        typed reply is classified: an ANSWER resolves the parked turn; a
        NEW_REQUEST gracefully cancels the clarify (the parked turn wakes with a
        distinct CANCELLED outcome — set the question aside, do NOT assume an
        answer) and the message runs as a fresh turn — so a user who pivots isn't
        silently answered with their unrelated message.

        Slash commands are never treated as answers; the ``/reset`` command
        additionally clears any pending clarify for the session. Never raises.
        """
        if route == "command":
            if target == _RESET_COMMAND:
                self._gateway.clear_session(session_id)
            return False, input_text

        # Is there a pending clarify for this session+channel? (read-only)
        pending = self._gateway.peek_for_session(session_id, channel)
        if pending is None:
            return False, input_text  # ordinary message — no clarify in flight

        # A clarify IS pending. Decide: does this typed reply answer it, or is it a
        # new request? (A button tap never reaches here — it resolves via the
        # callback path.) Classifier fail-safe → answer, so we never lose a reply.
        if self._classifier is not None:
            is_answer = await self._classifier.is_answer(
                question=pending.question, choices=pending.choices, message=input_text,
            )
            if not is_answer:
                # The user pivoted — cancel the parked turn gracefully (it wakes
                # with a DISTINCT cancelled outcome: set the question aside, no
                # assumption) and run this message as a fresh turn. Use
                # cancel_pending (a pivot), NOT clear_session (a teardown that
                # wakes as TIMED_OUT and would wrongly invite a best-guess).
                self._gateway.cancel_pending(session_id, channel)
                log.gateway.info(
                    "clarify_pump.resolve_or_rewrite: reply classified NEW_REQUEST — "
                    "clarify cancelled, running as a fresh turn",
                    extra={"_fields": {"session_id": session_id, "channel": channel}},
                )
                return False, input_text

        resolved = self._gateway.try_resolve(session_id, channel, input_text)
        if resolved is None:
            # Raced away (e.g. a button tap resolved it during classification).
            return False, input_text
        if resolved.event is not None and resolved.event.is_set():
            log.gateway.info(
                "clarify_pump.resolve_or_rewrite: reply resumed parked turn",
                extra={"_fields": {"session_id": session_id, "channel": channel}},
            )
            return True, input_text
        # Turn-yield fallback — re-inject the question + the user's reply so a
        # fresh turn can act on it (the parked-turn path is the primary one).
        log.gateway.info(
            "clarify_pump.resolve_or_rewrite: reply -> turn-yield resume",
            extra={"_fields": {"session_id": session_id}},
        )
        return False, (
            f"[Earlier you asked the user: {resolved.question}]\n"
            f"The user's reply: {input_text}\nContinue accordingly."
        )

    # ---------------------------------------------------------------- spawn-send

    def spawn_send(
        self,
        *,
        channel_adapter: _SendableAdapter,
        reader: AsyncIterator[ResponseChunk],
        session_id: str,
        request_id: str | None = None,
        producer: asyncio.Task[object],
        writer: StreamWriter | _ClosableWriter | None,
    ) -> None:
        """Drain the response stream in its own task; free the receive loop.

        Two DISTINCT keys (FF-E5-B2 / §4.1 stream re-key):

        * ``session_id`` keys the per-loop ``_inflight`` slot — the drain/reap
          ledger awaited on loop teardown. Same-session ordering is owned by the
          TurnRegistry now (§4.3), not this slot.
        * ``request_id`` (== the turn's ``trace_id``) keys the RESPONSE STREAM in
          the registry, matching the key :mod:`deliver` resolves the writer by
          (``state.trace_id``). The stream is reaped under THIS key. Defaults to
          ``session_id`` for back-compat when a caller doesn't supply one.

        ``producer`` is the turn task (``backend.run`` / parliament / command
        stub). If it crashes or is cancelled before :mod:`deliver` closes the
        writer, the send task would hang on a stream that never gets its sentinel
        and wedge the session — so a producer done-callback closes the writer
        (idempotent) to guarantee the send drains and the stream is reaped.
        """
        stream_key = request_id if request_id is not None else session_id
        send_task: asyncio.Task[None] = asyncio.create_task(
            channel_adapter.send(reader)
        )
        self._inflight[session_id] = send_task

        def _cleanup(
            task: asyncio.Task[None],
            sid: str = session_id,
            skey: str = stream_key,
        ) -> None:
            # Reap the STREAM by request_id (deliver's key); free the in-flight
            # SLOT by session_id.
            self._stream_registry.remove(skey)
            if self._inflight.get(sid) is task:
                self._inflight.pop(sid, None)

        send_task.add_done_callback(_cleanup)

        # B-1 — guarantee the writer is closed if the producer fails/cancels
        # before deliver does, so the send task can never hang the session.
        if writer is not None:
            def _close_on_producer_failure(
                prod: asyncio.Task[object], w: object = writer, sid: str = session_id,
            ) -> None:
                failed = prod.cancelled() or (
                    not prod.cancelled() and prod.exception() is not None
                )
                if not failed:
                    return
                log.gateway.warning(
                    "clarify_pump.spawn_send: producer failed before close — "
                    "closing writer so the send task drains",
                    extra={"_fields": {"session_id": sid}},
                )
                with contextlib.suppress(RuntimeError):
                    asyncio.create_task(self._safe_close(w))  # type: ignore[arg-type]

            producer.add_done_callback(_close_on_producer_failure)

    @staticmethod
    async def _safe_close(writer: _ClosableWriter) -> None:
        """Close a writer, swallowing errors (it may already be closed)."""
        try:
            await writer.close()
        except Exception as exc:  # self-healing — a double close is harmless
            log.gateway.debug(
                "clarify_pump._safe_close: close failed (likely already closed)",
                extra={"_fields": {"error": str(exc)}},
            )

    # ------------------------------------------------------------------ shutdown

    async def drain(self) -> None:
        """Await any in-flight send tasks (used on loop teardown)."""
        pending = [t for t in self._inflight.values() if not t.done()]
        for task in pending:
            with contextlib.suppress(Exception):
                await task
