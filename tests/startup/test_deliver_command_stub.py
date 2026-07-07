"""Plan C Task 2 — ``_deliver_command_stub``'s 3 dispatch branches.

``_deliver_command_stub`` is a nested closure inside
``StartupOrchestrator._phase_gateway`` (src/stackowl/startup/orchestrator.py),
so it cannot be imported and called directly. Following the established
pattern for testing this orchestrator's nested closures (see
``tests/startup/test_slack_gateway_wiring.py::_dispatch_turn`` and the P1/P2P3
journey tests' faithful re-creations of ``_dispatch_turn``), this test
byte-for-byte re-creates the closure body against REAL collaborators
(``CommandRegistry`` singleton, ``StreamRegistry``, real ``CommandResponse``/
``CommandNotFoundError``) — only ``sequence_store`` is fixed at ``None``
(best-effort learning, out of scope for the 3 dispatch-outcome branches this
covers).
"""

from __future__ import annotations

import logging

import pytest

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import Action, CommandResponse
from stackowl.exceptions import CommandNotFoundError
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry

pytestmark = pytest.mark.asyncio

_log = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()
    yield
    CommandRegistry.reset()


# ---- Faithful re-creation of orchestrator._deliver_command_stub -----------


async def _deliver_command_stub(
    cmd: str, session_id: str, state: PipelineState, args: str, trace_id: str,
    *, stream_registry: StreamRegistry,
) -> None:
    """Mirrors src/stackowl/startup/orchestrator.py::_deliver_command_stub.

    ``sequence_store`` is fixed at None here (its learning path is
    best-effort and orthogonal to the 3 dispatch-outcome branches under test).
    """
    registry = CommandRegistry.instance()
    writer = stream_registry.get_writer(trace_id)
    try:
        reply = await registry.dispatch(cmd, args, state)
    except CommandNotFoundError:
        text = f"Unknown slash command: '/{cmd}'. Try /help to see what's available."
        try:
            from stackowl.commands.resolver import suggest_invocations

            hits = await suggest_invocations(
                f"{cmd} {args}".strip(), registry.list(), limit=3
            )
            if hits:
                text += "\n\nDid you mean:\n" + "\n".join(f"  {h}" for h in hits)
        except Exception as exc:  # suggestion is best-effort
            _log.debug("command suggestion failed", exc_info=exc)
        reply = CommandResponse(text=text)
    except Exception as exc:
        _log.error("slash command failed", exc_info=exc)
        reply = CommandResponse(text=f"Command '/{cmd}' failed: {exc}")
    if writer is not None:
        await writer.write(ResponseChunk(
            content=reply.text, is_final=False, chunk_index=0,
            trace_id=trace_id, owl_name="system",
            actions=reply.actions,
        ))
        await writer.close()


# ---- Fake commands ----------------------------------------------------------


class _OkCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "ok"

    @property
    def description(self) -> str:
        return "always succeeds"

    async def handle(self, args: str, state: PipelineState) -> CommandResponse:
        return CommandResponse(
            text="did the thing", actions=(Action(label="Again", command="/ok"),)
        )


class _StatusCommand(SlashCommand):
    """Registered only so the CommandNotFoundError branch has a lexical match
    to surface via 'Did you mean'."""

    @property
    def command(self) -> str:
        return "status"

    @property
    def description(self) -> str:
        return "show status information"

    async def handle(self, args: str, state: PipelineState) -> str:
        return "status: ok"  # pragma: no cover — never dispatched in this suite


class _BoomCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "always raises"

    async def handle(self, args: str, state: PipelineState) -> str:
        raise RuntimeError("kaboom")


def _state(trace_id: str) -> PipelineState:
    return PipelineState(
        trace_id=trace_id, session_id="s1", input_text="/x",
        channel="cli", owl_name="system", pipeline_step="start",
    )


# ---- Branch 1: successful dispatch, actions carried through ---------------


async def test_successful_dispatch_carries_text_and_actions() -> None:
    CommandRegistry.instance().register(_OkCommand())
    stream_registry = StreamRegistry()
    writer, reader = stream_registry.create("t1")

    await _deliver_command_stub(
        "ok", "s1", _state("t1"), "", "t1", stream_registry=stream_registry,
    )

    chunks = [c async for c in reader]
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.content == "did the thing"
    assert chunk.actions == (Action(label="Again", command="/ok"),)


# ---- Branch 2: CommandNotFoundError, with and without a suggestion --------


async def test_unknown_command_reports_unknown_with_suggestion() -> None:
    CommandRegistry.instance().register(_StatusCommand())
    stream_registry = StreamRegistry()
    writer, reader = stream_registry.create("t2")

    await _deliver_command_stub(
        "nonexistent", "s1", _state("t2"), "status", "t2",
        stream_registry=stream_registry,
    )

    chunks = [c async for c in reader]
    assert len(chunks) == 1
    chunk = chunks[0]
    assert "Unknown slash command: '/nonexistent'" in chunk.content
    assert "Did you mean" in chunk.content
    assert "/status" in chunk.content
    assert chunk.actions == ()


async def test_unknown_command_no_suggestion_when_nothing_matches() -> None:
    # No commands registered at all — suggest_invocations has nothing to rank.
    stream_registry = StreamRegistry()
    writer, reader = stream_registry.create("t3")

    await _deliver_command_stub(
        "zzz", "s1", _state("t3"), "", "t3", stream_registry=stream_registry,
    )

    chunks = [c async for c in reader]
    assert len(chunks) == 1
    chunk = chunks[0]
    assert "Unknown slash command: '/zzz'" in chunk.content
    assert "Did you mean" not in chunk.content


# ---- Branch 3: generic Exception from handle() never propagates -----------


async def test_handler_exception_is_caught_and_reported() -> None:
    CommandRegistry.instance().register(_BoomCommand())
    stream_registry = StreamRegistry()
    writer, reader = stream_registry.create("t4")

    # Must not raise.
    await _deliver_command_stub(
        "boom", "s1", _state("t4"), "", "t4", stream_registry=stream_registry,
    )

    chunks = [c async for c in reader]
    assert len(chunks) == 1
    chunk = chunks[0]
    assert "failed" in chunk.content
    assert "kaboom" in chunk.content
    assert chunk.actions == ()
