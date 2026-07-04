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
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState


def _settings(*, live: bool) -> Any:
    return SimpleNamespace(progress=ProgressSettings(live_progress=live), decision_ledger=False)


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
