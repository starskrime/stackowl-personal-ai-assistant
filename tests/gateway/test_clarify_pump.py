"""Unit tests for :class:`ClarifyPump` — the real clarify-aware turn dispatch.

These exercise the SHIPPED pump (the gateway loops import the same class), not a
re-simulation. The load-bearing case is B-1: a producer task that crashes BEFORE
``deliver`` closes the writer must NOT leave the decoupled send task hanging and
wedge the session — the pump's producer guard closes the writer so the send
drains and the in-flight slot is reaped.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from stackowl.gateway.clarify_pump import ClarifyPump
from stackowl.pipeline.streaming import ResponseChunk, StreamReader, StreamRegistry

# --------------------------------------------------------------------- fakes


@dataclass
class _FakeEntry:
    question: str = "Which colour?"
    choices: tuple[str, ...] = ()
    event: asyncio.Event | None = None


class _FakeGateway:
    """Duck-typed ClarifyGateway surface the pump touches."""

    def __init__(self) -> None:
        self.peek_result: _FakeEntry | None = None
        self.resolve_result: _FakeEntry | None = None
        self.cleared: list[str] = []
        self.cancelled: list[tuple[str, str]] = []
        self.resolve_calls: list[tuple[str, str, str]] = []

    def peek_for_session(self, session_id: str, channel: str) -> _FakeEntry | None:
        return self.peek_result

    def try_resolve(self, session_id: str, channel: str, answer: str) -> _FakeEntry | None:
        self.resolve_calls.append((session_id, channel, answer))
        return self.resolve_result

    def clear_session(self, session_id: str) -> list[str]:
        self.cleared.append(session_id)
        return []

    def cancel_pending(self, session_id: str, channel: str) -> str | None:
        self.cancelled.append((session_id, channel))
        return None


class _FakeClassifier:
    """Stubs ClarifyIntentClassifier.is_answer with a fixed verdict."""

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict
        self.calls: list[str] = []

    async def is_answer(self, *, question: str, choices: object, message: str) -> bool:
        self.calls.append(message)
        return self._verdict


class _DrainingAdapter:
    """An adapter whose send drains the reader until the sentinel — like the real ones."""

    def __init__(self) -> None:
        self.chunks: list[str] = []

    async def send(self, reader: StreamReader) -> None:
        async for chunk in reader:
            self.chunks.append(chunk.content)


def _pump(classifier: _FakeClassifier | None = None) -> tuple[ClarifyPump, _FakeGateway, StreamRegistry]:
    gw = _FakeGateway()
    reg = StreamRegistry()
    return ClarifyPump(gw, reg, classifier), gw, reg  # type: ignore[arg-type]


# --------------------------------------------------------- resolve_or_rewrite


async def test_resolve_blocking_consumes_no_new_turn() -> None:
    pump, gw, _ = _pump()
    ev = asyncio.Event()
    ev.set()  # a blocking resolve: the parked waiter was woken
    gw.peek_result = _FakeEntry(event=ev)  # a clarify is pending
    gw.resolve_result = _FakeEntry(event=ev)
    consumed, text = await pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="owl", target="default", input_text="blue",
    )
    assert consumed is True
    assert text == "blue"


async def test_resolve_turn_yield_rewrites_input() -> None:
    pump, gw, _ = _pump()
    gw.peek_result = _FakeEntry(question="Which colour?", event=None)
    gw.resolve_result = _FakeEntry(question="Which colour?", event=None)  # turn-yield
    consumed, text = await pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="owl", target="default", input_text="blue",
    )
    assert consumed is False
    assert "Which colour?" in text and "blue" in text


async def test_no_pending_passes_through() -> None:
    pump, gw, _ = _pump()
    gw.peek_result = None  # no clarify in flight
    consumed, text = await pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="owl", target="default", input_text="hello",
    )
    assert consumed is False
    assert text == "hello"
    assert gw.resolve_calls == []  # never consulted try_resolve


async def test_command_is_never_an_answer_and_reset_clears() -> None:
    pump, gw, _ = _pump()
    gw.peek_result = _FakeEntry(event=asyncio.Event())  # would resolve if consulted
    consumed, text = await pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="command", target="reset", input_text="/reset",
    )
    assert consumed is False
    assert text == "/reset"
    assert gw.cleared == ["s1"]  # /reset cleared the pending clarify
    assert gw.resolve_calls == []  # try_resolve never consulted for a command


async def test_classifier_new_request_cancels_and_runs_fresh() -> None:
    # A clarify is pending; the typed reply is classified NEW_REQUEST → the pump
    # CANCELS the clarify (cancel_pending — a pivot, NOT clear_session, so the
    # parked waiter wakes CANCELLED not TIMED_OUT) and returns the message for a
    # fresh turn.
    classifier = _FakeClassifier(verdict=False)
    pump, gw, _ = _pump(classifier)
    gw.peek_result = _FakeEntry(question="Which colour?", event=asyncio.Event())
    consumed, text = await pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="owl", target="default",
        input_text="actually, what's the weather?",
    )
    assert consumed is False
    assert text == "actually, what's the weather?"  # original message, not swallowed
    assert gw.cancelled == [("s1", "cli")]  # parked turn cancelled via the PIVOT path
    assert gw.cleared == []  # NOT clear_session (that wakes TIMED_OUT, wrong outcome)
    assert gw.resolve_calls == []  # never resolved with the unrelated message
    assert classifier.calls == ["actually, what's the weather?"]


async def test_classifier_answer_resolves() -> None:
    # Classified ANSWER → resolves the parked turn as normal.
    classifier = _FakeClassifier(verdict=True)
    pump, gw, _ = _pump(classifier)
    ev = asyncio.Event()
    ev.set()
    gw.peek_result = _FakeEntry(event=ev)
    gw.resolve_result = _FakeEntry(event=ev)
    consumed, text = await pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="owl", target="default", input_text="blue",
    )
    assert consumed is True
    assert classifier.calls == ["blue"]
    assert gw.cleared == []  # NOT cancelled — it was a real answer
    assert gw.resolve_calls == [("s1", "cli", "blue")]


# ------------------------------------------------- non-blocking intake (§4.3)
#
# DELIBERATE change (concurrent-msg §4.3, Task 5): ``serialize_prior`` was DELETED
# by design — same-session ordering now lives in the TurnRegistry (at most one
# RUNNING turn per session + a FIFO intake queue), NOT in a per-session
# ``_inflight`` await-gate on the pump. This test is the replacement: it asserts
# the pump no longer carries that blocking gate (the non-blocking intake itself is
# exercised end to end against the real TurnRegistry in
# tests/gateway/test_nonblocking_intake.py). NOT weakened — the prior assertion
# (serialize_prior awaits the running turn) describes behavior that must NO LONGER
# exist, so asserting its absence is the faithful inverse.


async def test_serialize_prior_gate_is_gone() -> None:
    pump, _, _ = _pump()
    # The blocking serialize gate must be removed — intake never awaits a running
    # same-session turn on the pump anymore (the TurnRegistry queues it instead).
    assert not hasattr(pump, "serialize_prior")
    # _inflight survives ONLY as the drain/reap ledger for spawn_send + drain().
    assert pump._inflight == {}  # type: ignore[attr-defined]


# ----------------------------------------------------------------- spawn_send


async def test_spawn_send_drains_and_reaps_on_normal_close() -> None:
    pump, _, reg = _pump()
    # DELIBERATE re-key (§4.1): the stream registry is now keyed by request_id
    # (== trace_id), not session_id. Mint a request_id and drive the registry +
    # spawn_send bookkeeping by it; the slot is reaped under that request_id.
    request_id = "req-1"
    writer, reader = reg.create(request_id)
    adapter = _DrainingAdapter()

    async def _producer() -> None:
        await writer.write(
            ResponseChunk(
                content="hi", is_final=False, chunk_index=0, trace_id=request_id, owl_name="o"
            )
        )
        await writer.close()

    producer = asyncio.create_task(_producer())
    pump.spawn_send(
        channel_adapter=adapter, reader=reader, session_id=request_id,
        producer=producer, writer=writer,
    )
    await asyncio.wait_for(producer, 1.0)
    send_task = pump._inflight.get(request_id)  # type: ignore[attr-defined]
    assert send_task is not None
    await asyncio.wait_for(send_task, 1.0)
    await asyncio.sleep(0)  # let the send task's done-callback (_cleanup) run
    assert adapter.chunks == ["hi"]
    assert reg.get_writer(request_id) is None  # stream reaped, keyed by request_id
    assert request_id not in pump._inflight  # type: ignore[attr-defined]


async def test_spawn_send_does_not_wedge_when_producer_crashes_before_close() -> None:
    """B-1: a producer that raises WITHOUT closing the writer must not hang the send."""
    pump, _, reg = _pump()
    # DELIBERATE re-key (§4.1): registry keyed by request_id (== trace_id).
    request_id = "req-1"
    writer, reader = reg.create(request_id)
    adapter = _DrainingAdapter()

    async def _crashing_producer() -> None:
        # Crashes before deliver ever closes the writer.
        raise RuntimeError("pipeline blew up mid-turn")

    producer = asyncio.create_task(_crashing_producer())
    pump.spawn_send(
        channel_adapter=adapter, reader=reader, session_id=request_id,
        producer=producer, writer=writer,
    )
    # The producer crash is observed (retrieve the exception so no warning).
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(producer, 1.0)

    send_task = pump._inflight.get(request_id)  # type: ignore[attr-defined]
    assert send_task is not None
    # Without the B-1 guard this awaits forever (writer never closed) -> the
    # session is wedged. With the guard the writer is closed and send drains.
    await asyncio.wait_for(send_task, 1.0)
    await asyncio.sleep(0)  # let the send task's done-callback (_cleanup) run
    assert reg.get_writer(request_id) is None  # stream reaped, keyed by request_id
    assert request_id not in pump._inflight  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- F059


class _AlreadyClosedWriter:
    """A writer that reports it is already closing and raises on close()."""

    def is_closing(self) -> bool:
        return True

    async def close(self) -> None:
        raise RuntimeError("cannot close a closing transport")


class _BrokenWriter:
    """A writer that is NOT closed yet but fails to close (real failure)."""

    def is_closing(self) -> bool:
        return False

    async def close(self) -> None:
        raise OSError("transport flush failed")


async def test_safe_close_benign_already_closed_logs_debug(monkeypatch: object) -> None:
    """F059 — an already-closed writer's close error logs at DEBUG, never WARNING."""
    import stackowl.gateway.clarify_pump as cp

    debugs: list[str] = []
    warns: list[str] = []
    monkeypatch.setattr(cp.log.gateway, "debug", lambda msg, *a, **k: debugs.append(msg))  # type: ignore[attr-defined]
    monkeypatch.setattr(cp.log.gateway, "warning", lambda msg, *a, **k: warns.append(msg))  # type: ignore[attr-defined]

    await ClarifyPump._safe_close(_AlreadyClosedWriter())  # type: ignore[arg-type]
    assert len(debugs) == 1 and warns == [], (debugs, warns)


async def test_safe_close_unexpected_failure_logs_warning(monkeypatch: object) -> None:
    """F059 — a real close failure on a live writer WARNs so a stuck stream shows."""
    import stackowl.gateway.clarify_pump as cp

    debugs: list[str] = []
    warns: list[str] = []
    monkeypatch.setattr(cp.log.gateway, "debug", lambda msg, *a, **k: debugs.append(msg))  # type: ignore[attr-defined]
    monkeypatch.setattr(cp.log.gateway, "warning", lambda msg, *a, **k: warns.append(msg))  # type: ignore[attr-defined]

    # Must NOT raise (self-healing teardown), but must surface as a WARNING.
    await ClarifyPump._safe_close(_BrokenWriter())  # type: ignore[arg-type]
    assert len(warns) == 1 and debugs == [], (debugs, warns)
