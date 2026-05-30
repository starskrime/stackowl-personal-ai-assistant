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
    event: asyncio.Event | None = None


class _FakeGateway:
    """Duck-typed ClarifyGateway surface the pump touches."""

    def __init__(self) -> None:
        self.resolve_result: _FakeEntry | None = None
        self.cleared: list[str] = []
        self.resolve_calls: list[tuple[str, str, str]] = []

    def try_resolve(self, session_id: str, channel: str, answer: str) -> _FakeEntry | None:
        self.resolve_calls.append((session_id, channel, answer))
        return self.resolve_result

    def clear_session(self, session_id: str) -> list[str]:
        self.cleared.append(session_id)
        return []


class _DrainingAdapter:
    """An adapter whose send drains the reader until the sentinel — like the real ones."""

    def __init__(self) -> None:
        self.chunks: list[str] = []

    async def send(self, reader: StreamReader) -> None:
        async for chunk in reader:
            self.chunks.append(chunk.content)


def _pump() -> tuple[ClarifyPump, _FakeGateway, StreamRegistry]:
    gw = _FakeGateway()
    reg = StreamRegistry()
    return ClarifyPump(gw, reg), gw, reg  # type: ignore[arg-type]


# --------------------------------------------------------- resolve_or_rewrite


def test_resolve_blocking_consumes_no_new_turn() -> None:
    pump, gw, _ = _pump()
    ev = asyncio.Event()
    ev.set()  # a blocking resolve: the parked waiter was woken
    gw.resolve_result = _FakeEntry(event=ev)
    consumed, text = pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="owl", target="default", input_text="blue",
    )
    assert consumed is True
    assert text == "blue"


def test_resolve_turn_yield_rewrites_input() -> None:
    pump, gw, _ = _pump()
    gw.resolve_result = _FakeEntry(question="Which colour?", event=None)  # turn-yield
    consumed, text = pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="owl", target="default", input_text="blue",
    )
    assert consumed is False
    assert "Which colour?" in text and "blue" in text


def test_no_pending_passes_through() -> None:
    pump, gw, _ = _pump()
    gw.resolve_result = None
    consumed, text = pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="owl", target="default", input_text="hello",
    )
    assert consumed is False
    assert text == "hello"


def test_command_is_never_an_answer_and_reset_clears() -> None:
    pump, gw, _ = _pump()
    gw.resolve_result = _FakeEntry(event=asyncio.Event())  # would resolve if consulted
    consumed, text = pump.resolve_or_rewrite(
        session_id="s1", channel="cli", route="command", target="reset", input_text="/reset",
    )
    assert consumed is False
    assert text == "/reset"
    assert gw.cleared == ["s1"]  # /reset cleared the pending clarify
    assert gw.resolve_calls == []  # try_resolve never consulted for a command


# ------------------------------------------------------------ serialize_prior


async def test_serialize_prior_awaits_unfinished_same_session() -> None:
    pump, _, _ = _pump()
    done = asyncio.Event()

    async def _slow() -> None:
        await asyncio.sleep(0.02)
        done.set()

    pump._inflight["s1"] = asyncio.create_task(_slow())  # type: ignore[attr-defined]
    await pump.serialize_prior("s1")
    assert done.is_set()  # serialize_prior waited for the prior turn to finish


# ----------------------------------------------------------------- spawn_send


async def test_spawn_send_drains_and_reaps_on_normal_close() -> None:
    pump, _, reg = _pump()
    writer, reader = reg.create("s1")
    adapter = _DrainingAdapter()

    async def _producer() -> None:
        await writer.write(
            ResponseChunk(content="hi", is_final=False, chunk_index=0, trace_id="t", owl_name="o")
        )
        await writer.close()

    producer = asyncio.create_task(_producer())
    pump.spawn_send(
        channel_adapter=adapter, reader=reader, session_id="s1", producer=producer, writer=writer,
    )
    await asyncio.wait_for(producer, 1.0)
    send_task = pump._inflight.get("s1")  # type: ignore[attr-defined]
    assert send_task is not None
    await asyncio.wait_for(send_task, 1.0)
    await asyncio.sleep(0)  # let the send task's done-callback (_cleanup) run
    assert adapter.chunks == ["hi"]
    assert reg.get_writer("s1") is None  # stream reaped
    assert "s1" not in pump._inflight  # type: ignore[attr-defined]


async def test_spawn_send_does_not_wedge_when_producer_crashes_before_close() -> None:
    """B-1: a producer that raises WITHOUT closing the writer must not hang the send."""
    pump, _, reg = _pump()
    writer, reader = reg.create("s1")
    adapter = _DrainingAdapter()

    async def _crashing_producer() -> None:
        # Crashes before deliver ever closes the writer.
        raise RuntimeError("pipeline blew up mid-turn")

    producer = asyncio.create_task(_crashing_producer())
    pump.spawn_send(
        channel_adapter=adapter, reader=reader, session_id="s1", producer=producer, writer=writer,
    )
    # The producer crash is observed (retrieve the exception so no warning).
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(producer, 1.0)

    send_task = pump._inflight.get("s1")  # type: ignore[attr-defined]
    assert send_task is not None
    # Without the B-1 guard this awaits forever (writer never closed) -> the
    # session is wedged. With the guard the writer is closed and send drains.
    await asyncio.wait_for(send_task, 1.0)
    await asyncio.sleep(0)  # let the send task's done-callback (_cleanup) run
    assert reg.get_writer("s1") is None  # stream reaped, not wedged
    assert "s1" not in pump._inflight  # type: ignore[attr-defined]
