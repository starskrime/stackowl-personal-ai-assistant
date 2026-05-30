"""Tests for :class:`ClarifyTool` — blocking-await ask primitive.

Covers: non-interactive context → sentinel deny (no ask, no pending);
interactive → asks (gateway.ask called, pending exists, adapter got
send_clarify) and BLOCKS until a concurrent try_resolve delivers the answer →
result output carries the answer; interactive timeout → graceful in-turn timeout
result; missing channel/session → structured; gateway None → unavailable;
choices auto-append 'Other'; manifest severity/group; registered in
with_defaults.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.interaction.clarify import ClarifyTool
from stackowl.tools.registry import ToolRegistry


class _FakeAdapter:
    def __init__(self, name: str = "cli") -> None:
        self._name = name
        self.calls: list[tuple[str, tuple[str, ...], str]] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_clarify(
        self, question: str, choices: tuple[str, ...], clarify_id: str,
    ) -> None:
        self.calls.append((question, tuple(choices), clarify_id))


@pytest.fixture
def gateway() -> ClarifyGateway:
    gw = ClarifyGateway()
    gw.register_adapter("cli", _FakeAdapter("cli"))  # type: ignore[arg-type]
    return gw


@pytest.fixture
def with_gateway(gateway: ClarifyGateway) -> Iterator[ClarifyGateway]:
    token = set_services(StepServices(clarify_gateway=gateway))
    try:
        yield gateway
    finally:
        reset_services(token)


# --------------------------------------------------------------- interactive


async def test_interactive_blocks_then_returns_answer(with_gateway: ClarifyGateway) -> None:
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        # Launch the blocking execute as a task (it inherits this context).
        task = asyncio.ensure_future(
            ClarifyTool().execute(question="X or Y?", choices=["X", "Y"]),
        )
        # Let the tool register + park on the waiter, then deliver the reply.
        await asyncio.sleep(0)
        # Adapter received the question; the entry was registered as blocking.
        adapter = with_gateway._adapters["cli"]
        assert isinstance(adapter, _FakeAdapter)
        assert len(adapter.calls) == 1
        resolved = with_gateway.try_resolve("s1", "cli", "blue")
        assert resolved is not None  # a parked blocking waiter was woken
        result = await task
    finally:
        TraceContext.reset(trace)

    assert result.success is True
    # The tool's output IS the user's answer, framed for the model to continue.
    assert "blue" in result.output


async def test_interactive_graceful_timeout(with_gateway: ClarifyGateway) -> None:
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        # Tiny timeout, no reply → graceful in-turn timeout result.
        result = await ClarifyTool(timeout_s=0.05).execute(question="X or Y?")
    finally:
        TraceContext.reset(trace)

    assert result.success is True
    assert "did not reply in time" in result.output
    assert "ABORT" in result.output


async def test_choices_auto_append_other(with_gateway: ClarifyGateway) -> None:
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        task = asyncio.ensure_future(
            ClarifyTool().execute(question="pick?", choices=["A", "B"]),
        )
        await asyncio.sleep(0)  # let it register + park
        # Inspect the registered entry's choices, then resolve to unblock.
        entry = with_gateway.try_resolve("s1", "cli", "A")
        assert entry is not None
        assert entry.choices[:2] == ("A", "B")
        assert entry.choices[-1] == "Other (type your own)"
        await task
    finally:
        TraceContext.reset(trace)


# ----------------------------------------------------------- non-interactive


async def test_non_interactive_sentinel_no_ask(with_gateway: ClarifyGateway) -> None:
    trace = TraceContext.start(session_id="s1", interactive=False, channel="cli")
    try:
        result = await ClarifyTool().execute(question="should I?")
    finally:
        TraceContext.reset(trace)

    # Sentinel result that ABORTS on a consequential gate — never assumes.
    assert result.success is True
    assert "non-interactive" in result.output
    assert "ABORT" in result.output
    # Nothing was registered.
    assert with_gateway.try_resolve("s1", "cli", "x") is None


# ----------------------------------------------------------- context / errors


async def test_missing_channel_or_session_is_structured(with_gateway: ClarifyGateway) -> None:
    # Interactive but no channel/session in context.
    trace = TraceContext.start(session_id=None, interactive=True, channel=None)
    try:
        result = await ClarifyTool().execute(question="hmm?")
    finally:
        TraceContext.reset(trace)

    assert result.success is False
    assert "no channel context" in (result.error or "")
    assert with_gateway.try_resolve("s1", "cli", "x") is None


async def test_empty_question_is_structured(with_gateway: ClarifyGateway) -> None:
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        result = await ClarifyTool().execute(question="   ")
    finally:
        TraceContext.reset(trace)
    assert result.success is False
    assert "non-empty" in (result.error or "")


async def test_gateway_none_is_unavailable() -> None:
    token = set_services(StepServices(clarify_gateway=None))
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        result = await ClarifyTool().execute(question="q?")
    finally:
        TraceContext.reset(trace)
        reset_services(token)
    assert result.success is False
    assert "unavailable" in (result.error or "")


# ----------------------------------------------------------------- manifest


def test_manifest_severity_and_group() -> None:
    m = ClarifyTool().manifest
    assert m.name == "clarify"
    assert m.action_severity == "read"
    assert m.toolset_group == "interaction"


def test_registered_in_with_defaults() -> None:
    registry = ToolRegistry.with_defaults()
    assert any(t.name == "clarify" for t in registry.all())
