"""The decompose-before-retrying path has never run, because nothing ever
classified a failure.

MEASURED 2026-09-10 across every retained log: **117 of 117** `[loop] task
attempt failed` records carry `failure_class: ""`. `wants_reshaping` has
therefore never once been true, and `_maybe_reshape` — whose call site in
`loop.py` carries the comment *"CHANGE THE SHAPE BEFORE SPENDING ANOTHER
ATTEMPT"* and cites Bakir's task `43be4591` dying on
`budget:stop:steps:limit=20.0:actual=20.0` — has never reshaped anything.

THE CAUSE IS ONE STRING WITH TWO READERS THAT NEED OPPOSITE THINGS.
`task_loop_runner` raises the WORK's reason rather than the machinery's status,
and that was a deliberate, correct fix: `loop.py` stores the exception text as
`last_error` and `_augment_goal` shows it to the NEXT attempt as "what happened
last time", so "retry did not deliver (actuator reported 'pending')" was true and
useless. The model-facing reader was served.

The OTHER reader is `classify_failure`, which substring-matches the platform's own
markers — `budget:stop:…`. Those live in `final_state.errors`. The prose composed
for the model contains none of them, so the classifier read a sentence written for
somebody else and returned "" every time. It failed SILENTLY because "" means
retry, and the module's docstring says that default is deliberately the safe one —
so an unclassified failure looks exactly like an ordinary one.

The consequence is precisely what `loop.py`'s comment says must not happen:
re-running spends the same ceiling and stops at the same step. The same file
already records `8b7c4029` failing IDENTICALLY 74 times against a ceiling of 30.

THE FIX IS NOT A BETTER REGEX. The class is now named where the evidence still
exists — in the actuator, from `final_state.errors` — and rides the exception
beside `banned_capabilities`, which already travels that way for the same reason
("a raise is the only channel this runner has"). `classify_failure` prefers what
the producer named and keeps the substring table as the fallback for exceptions
that carry no attribute.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.failure_class import classify_failure, wants_reshaping

#: The real message, from the newest live record on 2026-09-10.
_LIVE_PROSE = (
    "the last attempt ran out of tokens after 35 tool calls; 9 of those calls were "
    "web_search back to back and it still did not finish — that path is exhausted, "
    "use a different one; these returned errors: web_fetch"
)
#: The marker the platform actually writes, as it appears in `final_state.errors`.
_LIVE_MARKER = "budget:stop:tokens:limit=1.0:actual=1.0"


@pytest.mark.tripwire
def test_the_prose_the_model_reads_carries_no_class() -> None:
    """THE DEFECT, pinned so the fix cannot be mistaken for a wording tweak.

    This is not something to repair by adding "ran out of tokens" to the
    substring table: the prose is composed per-attempt from the turn's evidence
    and its wording is free to change, which is exactly what the table's own
    comment warns about ("a regex that quietly stops matching downgrades a
    permanent error to retryable without anyone noticing").
    """
    assert classify_failure(RuntimeError(_LIVE_PROSE)) == ""


@pytest.mark.tripwire
def test_the_marker_the_machinery_writes_does_carry_it() -> None:
    """The evidence was never missing — it was in the other string."""
    assert classify_failure(_LIVE_MARKER) == "budget"
    assert wants_reshaping("budget") is True


@pytest.mark.tripwire
def test_a_named_class_beats_prose_and_reaches_reshaping() -> None:
    """THE REGRESSION. The exception keeps the model-facing prose AND classifies."""
    err = RuntimeError(_LIVE_PROSE)
    err.failure_class = "budget"  # type: ignore[attr-defined]
    assert classify_failure(err) == "budget"
    assert wants_reshaping(classify_failure(err)) is True
    assert str(err) == _LIVE_PROSE, "the model's reason must be untouched"


@pytest.mark.tripwire
def test_an_exception_without_the_attribute_still_uses_the_table() -> None:
    """THE CONTROL. The substring table is a fallback, not a casualty — every
    caller that raises a plain exception must classify exactly as before."""
    assert classify_failure(RuntimeError("connection refused")) == "transient"
    assert classify_failure(RuntimeError("403 forbidden")) == "auth"
    assert classify_failure(RuntimeError("nothing recognisable")) == ""


@pytest.mark.tripwire
def test_an_empty_or_junk_attribute_does_not_shadow_the_table() -> None:
    """Fail back, never fail closed. An attribute that is blank or not a string
    must not suppress a class the message can still supply — otherwise a
    half-wired producer would be WORSE than none."""
    for junk in ("", None, 0, [], {}):
        err = RuntimeError("connection refused")
        err.failure_class = junk  # type: ignore[attr-defined]
        assert classify_failure(err) == "transient", junk


@pytest.mark.tripwire
def test_the_actuator_names_the_class_where_the_evidence_is() -> None:
    """WIRED, NOT DECORATION — the shape this repo finds most often.

    A `failure_class` field nothing populates would look exactly like this fix
    and change nothing, which is the state the code was already in.

    THE ASSERTION READS THE CALL, NOT THE FILE. A substring check for
    "final_state.errors" passed a mutation that swapped the argument for
    `reason` — because the COMMENT above the call says "final_state.errors"
    too, and I wrote that comment. "A conjunction across a whole file is not a
    statement", at method scope: what is asserted here is the ARGUMENT of the
    `classify_failure` call.
    """
    import ast
    import inspect
    import textwrap

    from stackowl.pipeline import retry_actuator

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(retry_actuator.RetryActuator.attempt_retry))
    )
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "classify_failure"
    ]
    assert calls, "the actuator no longer names the class"
    args = " ".join(ast.dump(a) for c in calls for a in c.args)
    assert "final_state" in args and "errors" in args, (
        "classify_failure is called, but NOT on the markers — it must read "
        f"final_state.errors, not the prose composed for the model: {args[:200]}"
    )
    assert "failure_class" in inspect.getsource(retry_actuator.RetryOutcome)


@pytest.mark.tripwire
def test_the_runner_attaches_it_beside_banned_capabilities() -> None:
    """The channel already existed; this rides it rather than inventing one."""
    import inspect

    from stackowl.pipeline.durable import task_loop_runner

    src = inspect.getsource(task_loop_runner)
    assert "err.failure_class" in src
    assert "banned_capabilities" in src, (
        "the precedent this borrows must still be here — if it went, so did the "
        "argument for using the same channel"
    )
