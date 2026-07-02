"""REACT-7 / F092 + F099 — honesty backstops travel on structured records, not
ambient strings / implicit ContextVar lifetime.

F092: ``critical_failure`` derived the failed exception class by string-parsing
``state.errors`` (``"<step>: <ExcType>: <msg>"``). A drift in the backend's error
format would silently break critical-failure detection. The format contract now
lives in ONE shared helper (``step_error``) used by BOTH the writer (backends /
execute) and the reader (critical_failure), and a structured ``StepError`` record
is carried on PipelineState. A reader keyed on the structured field survives a
format change to the human-readable string.

F099: the consequential give-up tally + recovered set are SNAPSHOT onto immutable
state at end of execute (while the ledger ContextVar is live), and the floor reads
the snapshot when present — so the honesty decision travels with the state, not an
implicit bind() lifetime.
"""
from __future__ import annotations

import pytest

from stackowl.pipeline.state import PipelineState, StepError
from stackowl.pipeline.step_error import format_step_error, parse_step_error


# --------------------------------------------------------------------------- #
# F092 — shared format/parse contract + structured carrier
# --------------------------------------------------------------------------- #

def test_format_and_parse_roundtrip() -> None:
    err = format_step_error("execute", ValueError("boom: nested"))
    step, exc_type, msg = parse_step_error(err)
    assert step == "execute"
    assert exc_type == "ValueError"
    assert "boom" in msg


def test_critical_failure_reads_structured_step_error() -> None:
    """Even if the human string is reformatted, the structured record surfaces it."""
    from stackowl.pipeline.delivery_gate import detect_critical_failure

    # A REFORMATTED error string the old parser would no longer recognize
    # (prefixed with a trace id) — but the structured record names the step.
    state = PipelineState(
        trace_id="t", session_id="s", input_text="do it", channel="cli",
        owl_name="o", pipeline_step="deliver",
        errors=("[trace=abc] execute failed >> ValueError: boom",),
        step_errors=(StepError(step="execute", exc_type="ValueError", message="boom"),),
    )
    assert detect_critical_failure(state) is True


def test_critical_failure_string_fallback_still_works() -> None:
    """Back-compat: a turn with ONLY the legacy string (no structured record) still
    surfaces via the shared parser."""
    from stackowl.pipeline.delivery_gate import detect_critical_failure

    state = PipelineState(
        trace_id="t", session_id="s", input_text="do it", channel="cli",
        owl_name="o", pipeline_step="deliver",
        errors=("execute: RuntimeError: kaboom",),
    )
    assert detect_critical_failure(state) is True


def test_no_critical_failure_when_non_critical_step_errored() -> None:
    from stackowl.pipeline.delivery_gate import detect_critical_failure

    state = PipelineState(
        trace_id="t", session_id="s", input_text="do it", channel="cli",
        owl_name="o", pipeline_step="deliver",
        errors=("assemble: KeyError: x",),
        step_errors=(StepError(step="assemble", exc_type="KeyError", message="x"),),
    )
    assert detect_critical_failure(state) is False


# --------------------------------------------------------------------------- #
# F099 — consequential give-up snapshot on state
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_giveup_floor_reads_state_snapshot_without_ledger_binding() -> None:
    """The floor fires from the STATE snapshot even with NO live ledger binding —
    proving the honesty decision no longer depends on the ContextVar lifetime."""
    from stackowl.pipeline.delivery_gate import surface_consequential_giveup_floor

    # No tool_outcome_ledger.bind() here — the ContextVar is at its empty default.
    state = PipelineState(
        trace_id="t", session_id="s", input_text="send the email", channel="cli",
        owl_name="o", pipeline_step="execute",
        responses=(),
        consequential_failures=("send_email",),
        consequential_successes=(),
        recovered_consequential=(),
    )
    out = await surface_consequential_giveup_floor(state)
    assert out.responses, "the floor must produce an honest response from the snapshot"
    assert out.responses[-1].is_floor is True


@pytest.mark.asyncio
async def test_giveup_floor_snapshot_respects_substitution_recovery() -> None:
    """A consequential failure bridged by a substitution is NOT a give-up."""
    from stackowl.pipeline.delivery_gate import surface_consequential_giveup_floor

    state = PipelineState(
        trace_id="t", session_id="s", input_text="send the email", channel="cli",
        owl_name="o", pipeline_step="execute",
        responses=(),
        consequential_failures=("send_email",),
        consequential_successes=(),
        recovered_consequential=("send_email",),  # bridged by a sibling
    )
    out = await surface_consequential_giveup_floor(state)
    # No unrecovered consequential failure → no floor injected (state untouched).
    assert out is state
