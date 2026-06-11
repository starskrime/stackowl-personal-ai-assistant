"""TurnRouter wiring into the mid-turn arrival seam (concurrent-msg §6/§7, Task 16).

These tests drive the REAL route-and-act seam (``route_inflight_message``) against
the REAL :class:`TurnRouter` + :class:`TurnRegistry`, mocking ONLY the LLM
classifier (the fast-tier ``is_steer`` verdict) — mirroring the existing
``test_intake_drain_race`` / ``test_intake_cap_enforcement`` harness style (real
registry, controllable classifier, every await bounded by ``asyncio.wait_for``).

They assert the production routing behaviour Task 16 wires in:

  (a) explicit ``/steer X`` while a turn runs → ``try_steer`` folds ``X`` (token
      stripped) onto the running turn's mailbox — HANDLED, no queued-new.
  (b) explicit ``/stop`` → ``request_stop`` sets the running turn's stop flag —
      HANDLED.
  (c) explicit ``/new Y`` → queued-new (the helper returns ENQUEUE_NEW with ``Y``;
      the mirrored ``_intake`` NEW-path enqueues + instant-acks).
  (d) UNSIGNALED high-confidence steer (mock classifier → STEER) → ``try_steer``.
  (e) UNSIGNALED → NEW (mock classifier → NEW) → queued-new.
  (f) router error (classifier raises) → fail-safe queued-new (loudly logged).
  (g) the SLOW-ROUTE race: the turn FINISHES (status→FINALIZING) DURING the route,
      so a STEER signal becomes queued-new — ``try_steer`` returns ``"NEW"`` for the
      finished turn and enqueues it as a fresh turn, never a dead mailbox.

An idle session (no running turn) never reaches this helper — the orchestrator's
``_intake`` only calls it on the ``running is not None`` branch (asserted by the
``_intake``-mirror NEW path re-checking ``running()``), so the router adds zero
latency to idle sessions.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.inflight_router import (
    InflightAction,
    route_inflight_message,
    strip_signal_token,
)
from stackowl.gateway.turn_registry import TurnRegistry, TurnStatus
from stackowl.gateway.turn_router import ExplicitSignal, TurnRouter

pytestmark = pytest.mark.asyncio


class _MockClassifier:
    """Stand-in for ClarifyIntentClassifier.is_steer (the only LLM hop).

    ``verdict`` is the bool the high-confidence STEER-vs-NEW classifier returns;
    ``raises`` forces a classifier error to exercise the router's fail-safe.
    ``on_call`` is an optional async hook invoked INSIDE is_steer so a test can
    inject the slow-route race (finish the turn while the route is mid-flight).
    """

    def __init__(self, *, verdict: bool = False, raises: bool = False, on_call=None) -> None:
        self._verdict = verdict
        self._raises = raises
        self._on_call = on_call
        self.calls: list[tuple[str, str]] = []

    async def is_steer(self, *, running_ask: str, message: str) -> bool:
        self.calls.append((running_ask, message))
        if self._on_call is not None:
            await self._on_call()
        if self._raises:
            raise RuntimeError("classifier boom")
        return self._verdict


async def _register_running(reg: TurnRegistry, *, rid: str, sid: str, ask: str):
    """Register a long-lived RUNNING turn (a sleeping task stands in for the loop)."""
    task = asyncio.create_task(asyncio.sleep(5.0))
    turn = await reg.register(rid, session_id=sid, task=task, target=None, original_input=ask)
    return turn, task


async def _intake_new_path(reg: TurnRegistry, *, sid: str, text: str, rid: str) -> str:
    """Mirror the orchestrator _intake ENQUEUE_NEW branch (lock + re-check + enqueue).

    Re-acquires the per-session intake lock, re-checks running() (dispatch
    immediately if the turn finished), else enqueues as queued-new. Returns
    "dispatched" or "queued" so the test can assert the outcome.
    """
    async with reg.session_intake_lock(sid):
        if reg.running(sid) is None:
            # The running turn finished between route and re-acquire → dispatch now.
            await reg.register(
                rid, session_id=sid, task=asyncio.create_task(asyncio.sleep(0.01)),
                target=None, original_input=text,
            )
            return "dispatched"
        reg.enqueue(sid, original_input=text, request_id=rid, target=None)
        return "queued"


# --------------------------------------------------------------------------- #
# strip_signal_token — Task 14 review #4 (parser classifies, caller extracts).
# --------------------------------------------------------------------------- #


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_strip_signal_token_steer_and_new() -> None:
    assert strip_signal_token("/steer fix the import") == "fix the import"
    assert strip_signal_token("/new write the readme") == "write the readme"
    assert strip_signal_token("/STEER  CaseInsensitive") == "CaseInsensitive"
    assert strip_signal_token("/steer") == ""  # bare token, no body
    assert strip_signal_token("no signal here") == "no signal here"
    # /stop carries no body to fold; an unrelated slash is not stripped.
    assert strip_signal_token("/stop") == "/stop"
    assert strip_signal_token("/help me") == "/help me"


# --------------------------------------------------------------------------- #
# (a) explicit /steer → try_steer folds the stripped body.
# --------------------------------------------------------------------------- #


async def test_explicit_steer_folds_body_into_running_turn() -> None:
    reg = TurnRegistry()
    classifier = _MockClassifier(verdict=False)  # would say NEW; explicit must win
    router = TurnRouter(classifier)  # type: ignore[arg-type]
    turn, task = await _register_running(reg, rid="r1", sid="s1", ask="build the parser")
    try:
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=reg, running=turn,
                text="/steer use tabs not spaces", session_id="s1",
                request_id_new="r2", target=None,
            ),
            2.0,
        )
        assert outcome.action is InflightAction.HANDLED
        assert outcome.signal is ExplicitSignal.STEER
        # The classifier must NOT have been consulted (explicit signal short-circuits).
        assert classifier.calls == []
        # The stripped body landed in the running turn's mailbox.
        folded = turn.steering_mailbox.get_nowait()
        assert folded == "use tabs not spaces"
        assert turn.steering_mailbox.empty()
    finally:
        task.cancel()


# --------------------------------------------------------------------------- #
# (a2) STRING target (Slack thread_ts/channel) forwards through to try_steer.
#      A1-widen completion: route_inflight_message's `target` param must accept
#      int | str | None (not just int | None) so a Slack string steer reaches the
#      already-widened try_steer(target=...) unchanged on the mid-turn STEER path.
# --------------------------------------------------------------------------- #


async def test_string_target_forwards_into_try_steer() -> None:
    reg = TurnRegistry()
    router = TurnRouter(_MockClassifier(verdict=True))  # type: ignore[arg-type]
    turn, task = await _register_running(reg, rid="r1", sid="s1", ask="task A")

    captured: dict[str, object] = {}
    real_try_steer = reg.try_steer

    async def _spy_try_steer(request_id, text, *, session_id, request_id_new, target):  # type: ignore[no-untyped-def]
        captured["target"] = target
        return await real_try_steer(
            request_id, text,
            session_id=session_id, request_id_new=request_id_new, target=target,
        )

    reg.try_steer = _spy_try_steer  # type: ignore[method-assign]
    try:
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=reg, running=turn,
                text="/steer also handle slack", session_id="s1",
                request_id_new="r2", target="C123-1700000000.000100",
            ),
            2.0,
        )
        assert outcome.action is InflightAction.HANDLED
        assert outcome.signal is ExplicitSignal.STEER
        # The STRING target threaded through the seam into try_steer unchanged.
        assert captured["target"] == "C123-1700000000.000100"
    finally:
        task.cancel()


# --------------------------------------------------------------------------- #
# (b) explicit /stop → request_stop sets the running turn's stop flag.
# --------------------------------------------------------------------------- #


async def test_explicit_stop_sets_stop_flag() -> None:
    reg = TurnRegistry()
    router = TurnRouter(_MockClassifier())  # type: ignore[arg-type]
    turn, task = await _register_running(reg, rid="r1", sid="s1", ask="long job")
    try:
        assert turn.stop_requested is False
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=reg, running=turn,
                text="/stop", session_id="s1", request_id_new="r2", target=None,
            ),
            2.0,
        )
        assert outcome.action is InflightAction.HANDLED
        assert outcome.signal is ExplicitSignal.STOP
        assert turn.stop_requested is True
    finally:
        task.cancel()


# --------------------------------------------------------------------------- #
# (c) explicit /new Y → queued-new (helper ENQUEUE_NEW; _intake mirror enqueues).
# --------------------------------------------------------------------------- #


async def test_explicit_new_returns_enqueue_new_and_caller_queues() -> None:
    reg = TurnRegistry()
    router = TurnRouter(_MockClassifier())  # type: ignore[arg-type]
    turn, task = await _register_running(reg, rid="r1", sid="s1", ask="task A")
    try:
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=reg, running=turn,
                text="/new draft the email", session_id="s1",
                request_id_new="r2", target=None,
            ),
            2.0,
        )
        assert outcome.action is InflightAction.ENQUEUE_NEW
        assert outcome.signal is ExplicitSignal.NEW
        assert outcome.routed_text == "draft the email"  # /new token stripped
        # The mailbox must be untouched (never folded onto the running turn).
        assert turn.steering_mailbox.empty()
        # The caller's NEW path enqueues it (running turn still in flight).
        result = await _intake_new_path(reg, sid="s1", text=outcome.routed_text, rid="r2")
        assert result == "queued"
        nxt = reg.pop_next("s1")
        assert nxt is not None and nxt.original_input == "draft the email"
    finally:
        task.cancel()


# --------------------------------------------------------------------------- #
# (d) UNSIGNALED high-confidence steer (mock classifier STEER) → try_steer.
# --------------------------------------------------------------------------- #


async def test_unsignaled_high_conf_steer_folds() -> None:
    reg = TurnRegistry()
    classifier = _MockClassifier(verdict=True)  # high-confidence STEER
    router = TurnRouter(classifier)  # type: ignore[arg-type]
    turn, task = await _register_running(reg, rid="r1", sid="s1", ask="refactor the loader")
    try:
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=reg, running=turn,
                text="actually also handle the empty case", session_id="s1",
                request_id_new="r2", target=None,
            ),
            2.0,
        )
        assert outcome.action is InflightAction.HANDLED
        assert outcome.signal is ExplicitSignal.STEER
        assert classifier.calls  # the classifier WAS consulted (no explicit signal)
        folded = turn.steering_mailbox.get_nowait()
        assert folded == "actually also handle the empty case"  # no token to strip
    finally:
        task.cancel()


# --------------------------------------------------------------------------- #
# (e) UNSIGNALED → NEW (mock classifier NEW) → queued-new.
# --------------------------------------------------------------------------- #


async def test_unsignaled_new_returns_enqueue_new() -> None:
    reg = TurnRegistry()
    classifier = _MockClassifier(verdict=False)  # NEW
    router = TurnRouter(classifier)  # type: ignore[arg-type]
    turn, task = await _register_running(reg, rid="r1", sid="s1", ask="task A")
    try:
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=reg, running=turn,
                text="what's the weather in Paris", session_id="s1",
                request_id_new="r2", target=None,
            ),
            2.0,
        )
        assert outcome.action is InflightAction.ENQUEUE_NEW
        assert outcome.signal is ExplicitSignal.NEW
        assert turn.steering_mailbox.empty()  # never folded
        result = await _intake_new_path(reg, sid="s1", text=outcome.routed_text, rid="r2")
        assert result == "queued"
    finally:
        task.cancel()


# --------------------------------------------------------------------------- #
# (f) router error (classifier raises) → fail-safe queued-new.
# --------------------------------------------------------------------------- #


async def test_classifier_error_fail_safe_queued_new() -> None:
    reg = TurnRegistry()
    classifier = _MockClassifier(raises=True)  # the LLM hop blows up
    router = TurnRouter(classifier)  # type: ignore[arg-type]
    turn, task = await _register_running(reg, rid="r1", sid="s1", ask="task A")
    try:
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=reg, running=turn,
                text="ambiguous mid-turn message", session_id="s1",
                request_id_new="r2", target=None,
            ),
            2.0,
        )
        # The router internally fail-safes to NEW; the seam surfaces ENQUEUE_NEW.
        assert outcome.action is InflightAction.ENQUEUE_NEW
        assert outcome.signal is ExplicitSignal.NEW
        assert turn.steering_mailbox.empty()  # never mis-steered
        result = await _intake_new_path(reg, sid="s1", text=outcome.routed_text, rid="r2")
        assert result == "queued"
    finally:
        task.cancel()


# --------------------------------------------------------------------------- #
# (g) slow-route race: turn finishes (FINALIZING) during route → STEER → queued-new.
# --------------------------------------------------------------------------- #


async def test_slow_route_race_steer_on_finished_turn_becomes_queued_new() -> None:
    """The turn FINISHES (RUNNING→FINALIZING) while the slow LLM route is in flight.

    try_steer is atomic under the per-TURN lock: a STEER targeting a turn that is
    now past its finalization line returns "NEW" and enqueues the body as a fresh
    queued-new turn (never a dead mailbox). The seam reports HANDLED (try_steer
    already enqueued), and the queued-new turn is visible in the session queue.
    """
    reg = TurnRegistry()

    async def _finish_turn_mid_route() -> None:
        # Simulate the running turn crossing its finalization line DURING the route
        # (what finalize_and_drain does at the completion seam): flip RUNNING→FINALIZING.
        t = reg.running("s1")
        assert t is not None
        async with t.lock:
            t.status = TurnStatus.FINALIZING

    classifier = _MockClassifier(verdict=True, on_call=_finish_turn_mid_route)
    router = TurnRouter(classifier)  # type: ignore[arg-type]
    turn, task = await _register_running(reg, rid="r1", sid="s1", ask="task A")
    try:
        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router, registry=reg, running=turn,
                text="please also add logging", session_id="s1",
                request_id_new="r2", target=None,
            ),
            2.0,
        )
        # The seam reports HANDLED — try_steer saw FINALIZING and converted to NEW,
        # enqueuing the body itself; the body never landed in the dead mailbox.
        assert outcome.action is InflightAction.HANDLED
        assert turn.steering_mailbox.empty()
        # The converted queued-new turn is now in the session intake queue.
        nxt = reg.pop_next("s1")
        assert nxt is not None and nxt.original_input == "please also add logging"
        assert nxt.request_id == "r2"
    finally:
        task.cancel()
