"""Pipeline registry — ordered 8-step sequence."""

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
    parliament_step,
    synthesize,
    triage,
)

StepFn = Callable[[PipelineState], Coroutine[Any, Any, PipelineState]]

PIPELINE_STEPS: list[tuple[str, StepFn]] = [
    ("triage", triage.run),
    ("dispatch", dispatch.run),
    ("classify", classify.run),
    ("assemble", assemble.run),
    ("execute", execute.run),
    ("parliament_step", parliament_step.run),
    ("consolidate", consolidate.run),
    ("synthesize", synthesize.run),
]
