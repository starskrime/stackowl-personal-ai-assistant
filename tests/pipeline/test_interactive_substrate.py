"""E5 clarify substrate (B1) — interactive + pending_clarify_id plumbing.

State/context plumbing only: PipelineState carries `interactive` and
`pending_clarify_id`; TraceContext exposes `interactive` and `channel`; both
backends propagate state -> TraceContext. FAIL-CLOSED: `interactive` defaults to
False everywhere — a human is assumed absent unless a user-facing channel
explicitly declares True. Non-interactive sites (cron/parliament/A2A) ride that
False default (and keep explicit interactive=False for clarity); user-facing
channel adapters set interactive=True explicitly.
"""

from __future__ import annotations

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState


def _make_state(**overrides: object) -> PipelineState:
    base: dict[str, object] = {
        "trace_id": "t-1",
        "session_id": "s-1",
        "input_text": "hi",
        "channel": "cli",
        "owl_name": "secretary",
        "pipeline_step": "start",
    }
    base.update(overrides)
    return PipelineState(**base)  # type: ignore[arg-type]


# --- PipelineState fields -------------------------------------------------


def test_pipeline_state_interactive_defaults_false() -> None:
    # FAIL-CLOSED: human assumed absent unless a channel explicitly opts in.
    state = _make_state()
    assert state.interactive is False
    assert state.pending_clarify_id is None


def test_pipeline_state_interactive_can_be_true() -> None:
    state = _make_state(interactive=True)
    assert state.interactive is True


def test_pipeline_state_interactive_can_be_false() -> None:
    state = _make_state(interactive=False)
    assert state.interactive is False


def test_evolve_preserves_interactive_and_pending_id() -> None:
    state = _make_state(interactive=False, pending_clarify_id="abc")
    evolved = state.evolve(input_text="changed")
    assert evolved.input_text == "changed"
    assert evolved.interactive is False
    assert evolved.pending_clarify_id == "abc"


def test_evolve_can_update_interactive_and_pending_id() -> None:
    state = _make_state(interactive=True)
    evolved = state.evolve(interactive=False, pending_clarify_id="xyz")
    assert evolved.interactive is False
    assert evolved.pending_clarify_id == "xyz"
    # original is unchanged (frozen)
    assert state.interactive is True
    assert state.pending_clarify_id is None


def test_state_serialization_roundtrip_includes_new_fields() -> None:
    state = _make_state(interactive=False, pending_clarify_id="cid")
    dumped = state.model_dump()
    assert dumped["interactive"] is False
    assert dumped["pending_clarify_id"] == "cid"
    restored = PipelineState.model_validate(dumped)
    assert restored == state


# --- TraceContext API shape ----------------------------------------------


def test_trace_context_defaults_interactive_false_channel_none() -> None:
    # FAIL-CLOSED: start() without an explicit interactive=True assumes no human.
    token = TraceContext.start("sess")
    try:
        ctx = TraceContext.get()
        assert ctx["interactive"] is False
        assert ctx["channel"] is None
    finally:
        TraceContext.reset(token)


def test_trace_context_exposes_interactive_and_channel() -> None:
    token = TraceContext.start("sess", interactive=False, channel="cli")
    try:
        ctx = TraceContext.get()
        assert ctx["interactive"] is False
        assert ctx["channel"] == "cli"
    finally:
        TraceContext.reset(token)


def test_trace_context_token_reset_restores_previous() -> None:
    # Defaults outside any run (FAIL-CLOSED: interactive False).
    assert TraceContext.get()["interactive"] is False
    assert TraceContext.get()["channel"] is None
    token = TraceContext.start("sess", interactive=True, channel="telegram")
    TraceContext.reset(token)
    assert TraceContext.get()["interactive"] is False
    assert TraceContext.get()["channel"] is None


# --- Construction-site contracts -----------------------------------------


def test_cron_goal_execution_builds_non_interactive() -> None:
    import inspect

    from stackowl.scheduler.handlers import goal_execution

    src = inspect.getsource(goal_execution)
    # The cron handler's PipelineState construction must mark interactive=False.
    assert "interactive=False" in src


def test_parliament_round_builds_non_interactive() -> None:
    import inspect

    from stackowl.parliament import round_runner

    assert "interactive=False" in inspect.getsource(round_runner)


def test_a2a_delegation_builds_non_interactive() -> None:
    import inspect

    from stackowl.owls import a2a_delegation

    assert "interactive=False" in inspect.getsource(a2a_delegation)


def test_user_channel_state_is_interactive_when_declared() -> None:
    # FAIL-CLOSED: user-facing channel adapters must declare interactive=True
    # explicitly; a bare construction rides the safe False default.
    assert _make_state(channel="telegram").interactive is False
    assert _make_state(channel="telegram", interactive=True).interactive is True


# --- Backend propagation --------------------------------------------------


@pytest.mark.asyncio
async def test_asyncio_backend_propagates_interactive_and_channel() -> None:
    seen: dict[str, object] = {}

    async def _capturing_step(state: PipelineState) -> PipelineState:
        ctx = TraceContext.get()
        seen["interactive"] = ctx["interactive"]
        seen["channel"] = ctx["channel"]
        return state

    from stackowl.pipeline import registry as reg_module

    orig_steps = list(reg_module.PIPELINE_STEPS)
    reg_module.PIPELINE_STEPS[:] = [("capture", _capturing_step)]
    from stackowl.pipeline.steps import deliver as deliver_module

    orig_deliver_run = deliver_module.run

    async def _noop_deliver(s: PipelineState) -> PipelineState:
        return s

    deliver_module.run = _noop_deliver  # type: ignore[assignment]

    try:
        backend = AsyncioBackend(services=StepServices())
        state = _make_state(channel="cli", interactive=False)
        await backend.run(state)
    finally:
        reg_module.PIPELINE_STEPS[:] = orig_steps
        deliver_module.run = orig_deliver_run  # type: ignore[assignment]

    assert seen["interactive"] is False
    assert seen["channel"] == "cli"
    # Context reset after the run (FAIL-CLOSED default).
    assert TraceContext.get()["interactive"] is False


@pytest.mark.asyncio
async def test_asyncio_backend_propagates_interactive_true_inside_run() -> None:
    seen: dict[str, object] = {}

    async def _capturing_step(state: PipelineState) -> PipelineState:
        seen["interactive"] = TraceContext.get()["interactive"]
        return state

    from stackowl.pipeline import registry as reg_module

    orig_steps = list(reg_module.PIPELINE_STEPS)
    reg_module.PIPELINE_STEPS[:] = [("capture", _capturing_step)]
    from stackowl.pipeline.steps import deliver as deliver_module

    orig_deliver_run = deliver_module.run

    async def _noop_deliver(s: PipelineState) -> PipelineState:
        return s

    deliver_module.run = _noop_deliver  # type: ignore[assignment]

    try:
        backend = AsyncioBackend(services=StepServices())
        await backend.run(_make_state(channel="telegram", interactive=True))
    finally:
        reg_module.PIPELINE_STEPS[:] = orig_steps
        deliver_module.run = orig_deliver_run  # type: ignore[assignment]

    assert seen["interactive"] is True
