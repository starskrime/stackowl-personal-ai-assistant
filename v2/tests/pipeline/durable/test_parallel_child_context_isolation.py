"""4 concurrent children record their observed durable task_id — zero crossover (D1 §8.2).

Validates Break-A/B/C: asyncio.create_task snapshots the context, so each child's
TraceContext.get()["task_id"] is its OWN — even when one child raises and one is
cancelled. A backend whose run() stamps the durable scope (Task 9) is driven once
per child id via separate AsyncioBackend.run calls under separate states.

IMPLEMENTER NOTE (from plan): AsyncioBackend.run wraps each step in
``try/except Exception`` and folds failures into ``state.errors``, so a child whose
probe RAISES still completes run() — its observed task_id stays isolated. But
``asyncio.CancelledError`` is a ``BaseException`` in 3.11 (NOT ``Exception``), so a
genuinely cancelled probe would propagate out of run() and abort ``asyncio.gather``,
corrupting the test rather than exercising isolation. Per the plan's note, the "tr-d"
child raises ``RuntimeError`` (folded into errors, same as "tr-c"); genuine
cancellation isolation is covered by Task 16's a2a delegation test. The
no-crossover assertion is NOT weakened.
"""

from __future__ import annotations

import asyncio

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState

_observed: dict[str, str | None] = {}


async def test_four_concurrent_children_no_task_id_crossover(monkeypatch) -> None:  # noqa: ANN001
    import stackowl.pipeline.backends.asyncio_backend as mod

    # The PARENT frame holds no durable scope before/during/after the fan-out —
    # children stamp task_id in their OWN copied contexts (AsyncioBackend.run's
    # per-call TraceContext.start), never in this frame.
    assert TraceContext.get()["task_id"] is None

    # AsyncioBackend reads module-level PIPELINE_STEPS; one shared probe records
    # per-trace what THIS child observes, then forces a raise / a (would-be)
    # cancel branch so we prove isolation survives child failure too.
    async def _probe(state: PipelineState) -> PipelineState:
        _observed[state.trace_id] = TraceContext.get()["task_id"]
        if state.trace_id == "tr-c":
            raise RuntimeError("child boom")
        if state.trace_id == "tr-d":
            # Plan IMPLEMENTER NOTE: CancelledError is a BaseException in 3.11 and
            # would propagate out of run(), aborting gather. Use RuntimeError so
            # the child completes; genuine cancellation isolation = Task 16 a2a.
            raise RuntimeError("child cancelled")
        return state

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("probe", _probe)])

    async def _run(label: str, task_id: str) -> None:
        backend = AsyncioBackend(services=StepServices())
        state = PipelineState(
            trace_id=f"tr-{label}", session_id="s", input_text="x", channel="cli",
            owl_name="secretary", pipeline_step="", interactive=False,
            task_id=task_id, durable_owner_id="owner",
        )
        await backend.run(state)

    # NOTE: AsyncioBackend catches step exceptions into state.errors, so the
    # raising/"cancelled" children still complete run() — the assertion is purely
    # about task_id isolation in the observed map.
    await asyncio.gather(
        _run("a", "child-A"), _run("b", "child-B"),
        _run("c", "child-C"), _run("d", "child-D"),
    )

    # ZERO crossover: each child observed ITS OWN durable task_id, including the
    # raising ("tr-c") and the "cancelled" ("tr-d") children.
    assert _observed["tr-a"] == "child-A"
    assert _observed["tr-b"] == "child-B"
    assert _observed["tr-c"] == "child-C"
    assert _observed["tr-d"] == "child-D"

    # PARENT frame is still pristine after the fan-out — no child .set() leaked up.
    assert TraceContext.get()["task_id"] is None
