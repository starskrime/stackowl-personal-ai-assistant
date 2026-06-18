"""route_inflight_message — the mid-turn arrival router-and-act seam (concurrent-msg §6/§7, Task 16).

When a message arrives while a turn is already RUNNING for its session, the
orchestrator must decide what to DO with it: fold it into the running turn
(STEER), cooperatively halt the running turn (STOP), or start a fresh queued-new
turn (NEW). Tasks 11-15 built the pieces — :class:`TurnRouter` (the
STEER/STOP/NEW decision), :meth:`TurnRegistry.try_steer` (atomic fold-or-convert)
and :meth:`TurnRegistry.request_stop` (cooperative-stop flag). This module is the
thin, FULLY-TESTABLE seam that wires them together and is invoked by the
orchestrator's ``_intake`` once it has determined a turn is in-flight.

WHY a module-level helper (not inline in ``_intake``): the route→act decision is
the load-bearing P3 logic (the LLM route, the token-strip, the slow-route race,
the fail-safe). ``_intake`` is a deeply-nested closure inside the gateway phase
and the per-session lock discipline lives there; pulling the decision out lets it
be driven directly by an integration-ish test against the REAL ``TurnRouter`` +
``TurnRegistry`` (mocking only the LLM classifier), mirroring the existing
``test_intake_drain_race`` / ``test_intake_cap_enforcement`` harness pattern.

LOCK DISCIPLINE (CRITICAL — enforced by the CALLER, documented here): the
``TurnRouter.route`` call invokes an LLM (the STEER-vs-NEW classifier + the
optional turn-veto) and is SLOW. The orchestrator MUST NOT hold the per-session
intake lock across this call — holding it would block the session's
completion→drain seam. So ``_intake`` RELEASES the intake lock before calling
this helper. The three outcomes are each race-safe without the intake lock:

  * STEER → :meth:`TurnRegistry.try_steer` is atomic under the per-TURN lock and
    correctly converts to ``"NEW"`` (queued-new) if the turn FINISHED during the
    slow route (status FINALIZING/DONE) — so a turn finishing mid-route folds the
    instruction into a fresh queued-new turn rather than a dead mailbox.
  * STOP → :meth:`TurnRegistry.request_stop` is a synchronous flag-set, a no-op on
    an already-finished turn.
  * NEW → the caller re-acquires the intake lock briefly and RE-CHECKS
    ``running()`` (it may have finished → dispatch immediately) before enqueueing.

This helper performs the route + STEER/STOP actions and returns an
:class:`InflightOutcome` telling the caller whether to enqueue-as-new (the NEW
case, which needs the caller's lock + dispatch machinery) or that it already
acted (STEER/STOP). It NEVER raises — any router error fail-safes to NEW
(queued-new), loudly logged, so a broken router can never block the loop nor
mis-steer the running turn.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.gateway.turn_router import ExplicitSignal
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.gateway.turn_registry import Turn, TurnRegistry
    from stackowl.gateway.turn_router import TurnRouter


# Explicit-signal slash tokens whose BODY must be stripped before the routed text
# is folded/enqueued (Task 14 review point #4: `/steer X` / `/new X` route the
# BODY `X`, not the raw command line). Language-neutral command tokens — see
# turn_router for the multilingual rule. Kept in sync with the router's
# recognised slash signals.
_STRIP_TOKENS: tuple[str, ...] = ("/steer", "/new")


class InflightAction(enum.Enum):
    """What the caller must still do after :func:`route_inflight_message`.

    * ``HANDLED`` — the helper already acted (a STEER folded into the running
      turn's mailbox, or a STOP flag set). The caller does NOTHING further except
      the user notice it chooses to send.
    * ``ENQUEUE_NEW`` — the message must become a queued-new turn. The caller
      RE-ACQUIRES the per-session intake lock, re-checks ``running()`` (dispatch
      immediately if the turn finished), else enqueues + instant-acks. This is the
      ONE path that needs the caller's lock/dispatch machinery.
    """

    HANDLED = "handled"
    ENQUEUE_NEW = "enqueue_new"


@dataclass(frozen=True)
class InflightOutcome:
    """The result of routing a mid-turn message against the running turn.

    :param action: what the caller must still do (see :class:`InflightAction`).
    :param signal: the router's raw decision (for logging/telemetry at the call
        site); STEER/STOP/NEW.
    :param routed_text: the body the caller should enqueue when ``action`` is
        ``ENQUEUE_NEW`` — the explicit-signal token (``/steer``/``/new``) already
        stripped. For ``HANDLED`` it is the body that was folded/used (informational).
    """

    action: InflightAction
    signal: ExplicitSignal
    routed_text: str


def strip_signal_token(text: str) -> str:
    """Strip a leading ``/steer`` / ``/new`` explicit-signal token from ``text``.

    Task 14 review #4: the parser CLASSIFIES the signal; the caller EXTRACTS the
    body. ``/steer fix the import`` routes the body ``fix the import``; a bare
    ``/steer`` (no body) routes ``""``. Only the recognised STEER/NEW tokens are
    stripped (``/stop`` carries no body to fold; an unrelated ``/help`` is never a
    routing signal here so it is left intact). Case-insensitive on the token only;
    the body is returned verbatim (sans the single separating space). Pure, never
    raises.
    """
    stripped = text.lstrip()
    lowered = stripped.lower()
    for token in _STRIP_TOKENS:
        if lowered == token:
            # Bare token, no body.
            return ""
        prefix = token + " "
        if lowered.startswith(prefix):
            return stripped[len(prefix):].lstrip()
    return text


async def route_inflight_message(
    *,
    router: TurnRouter,
    registry: TurnRegistry,
    running: Turn,
    text: str,
    session_id: str,
    request_id_new: str,
    target: int | str | None,
    is_reply_to_inflight: bool = False,
) -> InflightOutcome:
    """Route a mid-turn ``text`` against the ``running`` turn and act on STEER/STOP.

    Called by ``_intake`` ONLY when a turn is in-flight, and ONLY after the
    per-session intake lock has been RELEASED (the router call is a slow LLM hop —
    see the module docstring's lock discipline). Resolves the message to
    STEER/STOP/NEW via the ``router`` (which short-circuits explicit signals at
    zero LLM cost and fail-safes to NEW on any classifier/veto error), then:

      * STEER → strip the ``/steer`` token and :meth:`TurnRegistry.try_steer` the
        body onto the running turn's mailbox. ``try_steer`` is atomic under the
        per-TURN lock: if the turn FINISHED during the slow route it returns
        ``"NEW"`` (it enqueued the body as a queued-new turn itself) — so the
        slow-route race folds into a fresh turn, never a dead mailbox. Either way
        the steer/convert is HANDLED here (the enqueue, if any, is done by
        ``try_steer``); the caller does nothing further.
      * STOP → :meth:`TurnRegistry.request_stop` (cooperative flag, no-op if the
        turn already finished). HANDLED.
      * NEW → strip the ``/new`` token (if present) and return ``ENQUEUE_NEW`` so
        the caller (which owns the intake lock + dispatch machinery) enqueues the
        body as a queued-new turn under a fresh re-check of ``running()``.

    Fail-safe: ANY unexpected error collapses to ``ENQUEUE_NEW`` (queued-new),
    loudly logged. Never raises — a broken router can never block the loop nor
    mis-steer the running turn.
    """
    # 1. ENTRY
    log.gateway.debug(
        "[router] route_inflight_message: entry",
        extra={
            "_fields": {
                "session_id": session_id,
                "running_request_id": running.turn_id,
                "text_len": len(text),
                "is_reply": is_reply_to_inflight,
            }
        },
    )
    try:
        # The router invokes the LLM (is_steer + optional veto); it fail-safes to
        # NEW internally on any classifier/veto error and never raises.
        signal = await router.route(
            running_ask=running.original_input,
            message=text,
            is_reply_to_inflight=is_reply_to_inflight,
        )

        # 2. DECISION — act on the routed signal.
        if signal is ExplicitSignal.STEER:
            body = strip_signal_token(text)
            # try_steer is atomic under the per-TURN lock; if the turn finished
            # during the slow route it converts to queued-new ("NEW") itself.
            steer_result = await registry.try_steer(
                running.turn_id, body,
                session_id=session_id, request_id_new=request_id_new, target=target,
            )
            log.gateway.info(
                "[router] route_inflight_message: STEER → try_steer",
                extra={"_fields": {
                    "session_id": session_id, "running_request_id": running.turn_id,
                    "try_steer_result": steer_result,
                }},
            )
            # Whether it folded (STEER) or the turn finished mid-route and it
            # enqueued a queued-new turn (NEW), try_steer already did the enqueue.
            # The caller does NOT re-enqueue.
            return InflightOutcome(
                action=InflightAction.HANDLED, signal=ExplicitSignal.STEER, routed_text=body,
            )

        if signal is ExplicitSignal.STOP:
            registry.request_stop(running.turn_id)
            log.gateway.info(
                "[router] route_inflight_message: STOP → request_stop",
                extra={"_fields": {
                    "session_id": session_id, "running_request_id": running.turn_id,
                }},
            )
            return InflightOutcome(
                action=InflightAction.HANDLED, signal=ExplicitSignal.STOP, routed_text="",
            )

        # NEW (or any other signal) → queued-new. Strip a leading /new token.
        body = strip_signal_token(text)
        log.gateway.info(
            "[router] route_inflight_message: NEW → enqueue queued-new",
            extra={"_fields": {
                "session_id": session_id, "running_request_id": running.turn_id,
            }},
        )
        return InflightOutcome(
            action=InflightAction.ENQUEUE_NEW, signal=ExplicitSignal.NEW, routed_text=body,
        )
    except Exception as exc:  # self-healing — routing must NEVER block/crash intake.
        log.gateway.error(
            "[router] route_inflight_message: failed — fail-safe queued-new",
            exc_info=exc,
            extra={"_fields": {"session_id": session_id, "text_len": len(text)}},
        )
        return InflightOutcome(
            action=InflightAction.ENQUEUE_NEW,
            signal=ExplicitSignal.NEW,
            routed_text=strip_signal_token(text),
        )
