"""Global interactive turn deadline (system.interactive_turn_timeout_s).

2026-07 incident: an interactive telegram turn hung 1670+s because no
turn-level deadline existed — only lower-level timeouts (resilient_round,
parliament, evolution). The deadline bounds ONLY interactive turns; scheduled /
parliament / delegation / evolution runs keep their own budgets.
"""

from __future__ import annotations

import asyncio
import time

from stackowl.config.settings import Settings, SystemSettings
from stackowl.memory.outcome_store import classify_failure
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _settings(timeout_s: float) -> Settings:
    # NOTE: Settings() kwargs are silently ignored — use model_copy to override.
    return Settings().model_copy(
        update={"system": SystemSettings(interactive_turn_timeout_s=timeout_s)}
    )


def _state(*, interactive: bool) -> PipelineState:
    return PipelineState(
        trace_id="tr-deadline", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="", interactive=interactive,
    )


async def _hung_step(state: PipelineState) -> PipelineState:
    await asyncio.sleep(30.0)  # simulates a wedged tool/provider call
    return state


def _answer_step(text: str):  # noqa: ANN202
    async def _step(state: PipelineState) -> PipelineState:
        return state.evolve(responses=(*state.responses, ResponseChunk(
            content=text, is_final=False, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        )))
    return _step


async def test_hung_interactive_turn_ends_at_deadline(monkeypatch) -> None:  # noqa: ANN001
    import stackowl.pipeline.backends.asyncio_backend as mod

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("execute", _hung_step)])
    backend = AsyncioBackend(services=StepServices(settings=_settings(0.2)))
    t0 = time.monotonic()
    final = await backend.run(_state(interactive=True))
    assert time.monotonic() - t0 < 5.0  # ended at the deadline, not after 30s

    # Failure recorded honestly — classify_failure sees a real failure class.
    assert any(e.startswith("deadline:") for e in final.errors)
    assert any(se.step == "deadline" and se.exc_type == "TimeoutError"
               for se in final.step_errors)
    assert classify_failure(final.errors) == "TimeoutError"

    # Honest floor message delivered — never silent, never a fake answer.
    floors = [c for c in final.responses if c.is_floor]
    assert floors and floors[0].content.strip()


async def test_fast_interactive_turn_unaffected(monkeypatch) -> None:  # noqa: ANN001
    import stackowl.pipeline.backends.asyncio_backend as mod

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("execute", _answer_step("ok!"))])
    with_deadline = AsyncioBackend(services=StepServices(settings=_settings(60.0)))
    no_settings = AsyncioBackend(services=StepServices())  # deadline disabled

    a = await with_deadline.run(_state(interactive=True))
    b = await no_settings.run(_state(interactive=True))

    assert a.errors == b.errors == ()
    assert [c.content for c in a.responses] == [c.content for c in b.responses]
    assert not any(c.is_floor for c in a.responses)


async def test_non_interactive_run_never_cut(monkeypatch) -> None:  # noqa: ANN001
    import stackowl.pipeline.backends.asyncio_backend as mod

    async def _slow_but_legit(state: PipelineState) -> PipelineState:
        await asyncio.sleep(0.5)  # well past the 0.1s deadline below
        return await _answer_step("scheduled done")(state)

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("execute", _slow_but_legit)])
    backend = AsyncioBackend(services=StepServices(settings=_settings(0.1)))
    final = await backend.run(_state(interactive=False))

    assert final.errors == ()
    assert [c.content for c in final.responses] == ["scheduled done"]


async def test_zero_timeout_disables_deadline(monkeypatch) -> None:  # noqa: ANN001
    import stackowl.pipeline.backends.asyncio_backend as mod

    async def _slow(state: PipelineState) -> PipelineState:
        await asyncio.sleep(0.3)
        return await _answer_step("slow ok")(state)

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("execute", _slow)])
    backend = AsyncioBackend(services=StepServices(settings=_settings(0.0)))
    final = await backend.run(_state(interactive=True))

    assert final.errors == ()
    assert [c.content for c in final.responses] == ["slow ok"]
