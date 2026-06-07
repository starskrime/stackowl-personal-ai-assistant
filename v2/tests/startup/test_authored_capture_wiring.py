"""Wiring test: capture_authored_dna is called before hydrate_dna in orchestrator."""

import inspect
from stackowl.startup import orchestrator


def test_capture_authored_runs_before_hydrate():
    src = inspect.getsource(orchestrator)
    assert "capture_authored_dna" in src
    assert src.index("capture_authored_dna") < src.index("hydrate_dna("), (
        "authored capture must precede hydrate"
    )
