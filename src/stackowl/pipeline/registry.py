"""Pipeline registry — ordered 7-step sequence (``deliver`` runs after)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import (
    assemble,
    classify,
    consolidate,
    dispatch,
    execute,
    feedback,
    parliament_step,
    triage,
)

StepFn = Callable[[PipelineState], Coroutine[Any, Any, PipelineState]]

# F-2 (DEFERRED — architectural) — this fixed sequence has no dedicated
# plan/decompose stage ahead of ``execute``. Decomposition is NOT absent from the
# platform: it is owned today by the OBJECTIVES PLANNER (``stackowl.objectives`` —
# ``decomposer.py`` splits a goal into ordered sub-goals, ``driver.py`` advances
# them, ``store.py`` persists state), reached out-of-band via the ``objective``
# tool / durable goals rather than inline in this per-turn pipeline. Inserting a
# plan/decompose step here would restructure the hot path and duplicate that
# authority, so it is intentionally NOT done as part of the S3 (minor) pass: a
# safe version needs its own design (where the planner's output threads into
# ``execute``, how it stays flag-OFF/byte-identical by default, and how it avoids
# two decomposition owners). Until then the objectives planner remains the single
# decomposition path. Do not fake a stage here.
PIPELINE_STEPS: list[tuple[str, StepFn]] = [
    ("triage", triage.run),
    ("dispatch", dispatch.run),
    ("classify", classify.run),
    ("assemble", assemble.run),
    # LS4 — capture a reaction to the LAST render into the durable output_style
    # preference (aspect-scoped) BEFORE execute. After classify (history carries
    # the prior render) and assemble; on a handled reaction it stamps
    # ``feedback_handled`` + a confirmation so execute short-circuits the tool loop.
    ("feedback", feedback.run),
    ("execute", execute.run),
    ("parliament_step", parliament_step.run),
    ("consolidate", consolidate.run),
]
