"""Task 2 — the progress-start ack fires BEFORE triage's LLM router call.

Root cause (see .superpowers/sdd/task-2-brief.md): `is_eligible()` only reads
gateway-populated state, so it never needed triage/classify/assemble to have
run first — but the ack call site lived deep inside execute.py's tool loop,
firing well after the router call, an embedding call, and memory/graph reads
had already run unacked. This asserts the ack now fires from the backend loop
BEFORE the first pipeline step (triage) executes, for an eligible turn, and
that an ineligible turn still emits nothing (no eligibility behavior change).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from stackowl.config.progress_settings import ProgressSettings
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.progress.emitter import (
    PIPELINE_STEP_EVENT,
    bind_turn_callback,
    emit_start,
    make_progress_callback,
    reset_turn_callback,
)
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.registry import ToolRegistry


class _FakeBus:
    """Minimal EventBus stand-in — records every (event, payload) emitted."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))


def _settings(*, live: bool) -> Any:
    return SimpleNamespace(
        progress=ProgressSettings(live_progress=live),
        decision_ledger=False,
        # _run_with_tools reads settings.orchestrator.tool_count_cap unguarded
        # (no try/except) — only the continuity test below drives that far.
        orchestrator=SimpleNamespace(tool_count_cap=40),
    )


def _state(**over: Any) -> PipelineState:
    base: dict[str, Any] = dict(
        trace_id="t1",
        session_id="s1",
        input_text="hi",
        channel="cli",
        owl_name="Athena",
        pipeline_step="",
        interactive=True,
    )
    base.update(over)
    return PipelineState(**base)


async def test_ack_fires_before_triage_router_call(monkeypatch) -> None:  # noqa: ANN001
    import stackowl.pipeline.backends.asyncio_backend as mod

    order: list[str] = []

    async def _fake_triage(state: PipelineState) -> PipelineState:
        order.append("triage")
        return state

    async def _fake_emit_progress_start(cb: Any) -> None:
        if cb is not None:
            order.append("ack")

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("triage", _fake_triage)])
    monkeypatch.setattr(mod, "emit_progress_start", _fake_emit_progress_start, raising=False)

    backend = AsyncioBackend(services=StepServices(settings=_settings(live=True)))
    await backend.run(_state())

    assert order == ["ack", "triage"], f"expected ack before triage's router call, got {order}"


async def test_no_ack_when_not_eligible(monkeypatch) -> None:  # noqa: ANN001
    """Flag OFF ⇒ is_eligible() False ⇒ no emission at all (byte-identical gating)."""
    import stackowl.pipeline.backends.asyncio_backend as mod

    order: list[str] = []

    async def _fake_triage(state: PipelineState) -> PipelineState:
        order.append("triage")
        return state

    async def _fake_emit_progress_start(cb: Any) -> None:
        if cb is not None:
            order.append("ack")

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("triage", _fake_triage)])
    monkeypatch.setattr(mod, "emit_progress_start", _fake_emit_progress_start, raising=False)

    backend = AsyncioBackend(services=StepServices(settings=_settings(live=False)))
    await backend.run(_state())

    assert order == ["triage"], f"expected no ack when ineligible, got {order}"


class _OneIterationProvider:
    """A provider whose ``complete_with_tools`` fires ONE ReAct iteration event
    through whatever ``on_iteration_complete`` execute.py composed — exactly what
    a real tool-loop turn does, without needing a real model/tool round-trip."""

    protocol = "anthropic"

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs: object,
    ):
        if on_iteration_complete is not None:
            await on_iteration_complete(ReActIterationState(
                iteration=0,
                tool_call_records=[{"name": "web_search", "args": {}, "result": "ok", "failed": False}],
            ))
        return ("done", [])


async def test_execute_reuses_bound_turn_callback_for_continuity() -> None:
    """The ack and the first REAL tool-loop iteration share ONE step_index counter.

    Reviewer finding: two independent `_ProgressEmitter` instances (one built in
    asyncio_backend.py for the pre-loop ack, a second built later in execute.py
    for per-iteration updates) each start their own counter at 0 — so the TUI's
    PipelineStrip glyph "train" (`tui/widgets/pipeline_strip.py:92`, `i <
    step_index`) fills to 1 after the ack, then STAYS at 1 instead of advancing to
    2 on the first real tool-call progress event.

    Fixed by binding the ack's callback into a turn-scoped ContextVar
    (`bind_turn_callback`/`get_turn_callback`) that execute.py's `_run_with_tools`
    now reuses instead of building a second emitter. Unlike the call-order tests
    above, this drives the REAL `_run_with_tools` (execute.py's actual code, not a
    fake step standing in for it) through a fake provider that fires one ReAct
    iteration via the real composed `on_iteration_complete` callback — so this
    exercises the exact `_progress_cb = get_turn_callback() or
    make_progress_callback(...)` line the fix touched.
    """
    bus = _FakeBus()
    registry = ToolRegistry()
    state = PipelineState(
        trace_id="trace-cont", session_id="sess-cont", input_text="go",
        channel="cli", owl_name="owl", pipeline_step="execute", interactive=True,
    )
    services = StepServices(event_bus=bus, tool_registry=registry, settings=_settings(live=True))

    services_token = set_services(services)
    ack_cb = make_progress_callback(state, services)  # what asyncio_backend builds
    assert ack_cb is not None
    turn_token = bind_turn_callback(ack_cb)  # what asyncio_backend binds pre-loop
    try:
        await emit_start(ack_cb)  # the pre-loop ack itself
        await _run_with_tools(state, _OneIterationProvider(), registry)  # execute.py's real code
    finally:
        reset_turn_callback(turn_token)
        reset_services(services_token)

    step_indexes = [
        payload["step_index"] for (event, payload) in bus.events if event == PIPELINE_STEP_EVENT
    ]
    assert step_indexes == [1, 2], (
        f"expected a continuous step_index across the ack→first-iteration boundary, got {step_indexes}"
    )
