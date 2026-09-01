"""A retry must be told what the WORK hit, not what the machinery did.

THE PLATFORM DIAGNOSED THIS ITSELF and Bakir sent the verdict back as the item.
Its own verified RCA on ``web_fetch:stop``:

    "The retry loop re-executes an unchanged webfetch-first strategy on the same
    live-verification task, and it floors every time: all 5 supplied traces are
    retries 2-6, 4 share the identical task text, webfetch is the first/primary
    call in all 5 (aggregate 125 webfetch vs 92 websearch), websearch
    substitution fired in all 5 without resolving, and each retry burned 44-52
    calls before flooring to stop."

Its first recommended fix: "retry prompts must append the prior failure signal
instead of re-sending identical task text".

THE CHANNEL FOR THAT ALREADY EXISTED, WIRED END TO END, CARRYING A TAUTOLOGY.
``_augment_goal`` is Bakir's own contract in code — "next loop when it picks it,
it also looks: is any previous one? Yes — learn from that experience" — and it
faithfully shows the next attempt whatever ``last_error`` holds. MEASURED
2026-09-01 across the live task table:

* of 82 tasks with a failed attempt, **80 (98%) carried no banned capability**;
* every recorded ``last_error`` but one described the MACHINERY, not the work:
  72 x "retired by the unreachable-owner sweep", 5 x "retry did not deliver
  (actuator reported 'pending')", 4 x "budget:stop:...";
* the floored-retry path wrote the literal string ``"retry attempt still
  floored"`` — the RCA's own "four still floored markers".

"It failed" is not a strategy signal. An attempt cannot change course when the
only thing it is told about the last one is that it did not work.

TWO SEAMS, ONE CAUSE, BOTH FIXED HERE. The actuator described the turn in its
own vocabulary ("still floored") and the durable runner re-described the outcome
in its ("actuator reported 'pending'"), overwriting anything below it. Each is
the same mistake: the layer reporting the failure is the one furthest from the
evidence.

AND THE BAN LANDED ON THE WRONG TOOL. ``_pick_newly_failed`` returned "the FIRST
capability this attempt touched", a positional guess. Measured: a task banned
``send_message`` over a tool_sequence of ``["search_files","search_files",
"read_file"]``, and another banned ``shell`` over ``["read_file"]``. The next
attempt was steered away from something innocent while the real dead end stayed
open. ``consequential_failures`` is the turn's own record of what actually
failed and was there all along.
"""

from __future__ import annotations

from stackowl.pipeline.delivery_gate import (
    describe_attempt_evidence,
    failed_capabilities_for_state,
)
from stackowl.pipeline.retry_actuator import RetryOutcome


class _State:
    """Only the fields the two helpers read, in the snapshot shape."""

    def __init__(
        self,
        *,
        failures: tuple[str, ...] = (),
        successes: tuple[str, ...] = (),
        recovered: tuple[str, ...] = (),
    ) -> None:
        self.consequential_failures = failures
        self.consequential_successes = successes
        self.recovered_consequential = recovered
        self.trace_id = "t-1"

    @property
    def has_consequential_snapshot(self) -> bool:
        return True


# --------------------------------------------------------------------------- #
# The evidence sentence                                                        #
# --------------------------------------------------------------------------- #


def test_the_retry_is_told_which_tools_did_not_resolve_it() -> None:
    """The live shape from the RCA: a web_fetch-first attempt that floored."""
    state = _State(
        failures=("web_fetch", "web_search"), successes=("browser_navigate",),
    )
    evidence = describe_attempt_evidence(state)
    assert "web_fetch" in evidence and "web_search" in evidence
    assert "did not resolve it" in evidence
    assert evidence != "retry attempt still floored"


def test_the_sentence_names_everything_the_attempt_tried() -> None:
    """Not only the failures — "it tried X and Y" is what lets the next attempt
    pick something it has NOT already burned."""
    state = _State(failures=("web_fetch",), successes=("read_file",))
    evidence = describe_attempt_evidence(state)
    assert "read_file" in evidence and "web_fetch" in evidence


def test_a_bridged_failure_is_not_reported_as_a_dead_end() -> None:
    """A substitution that RECOVERED is not a give-up. Naming it would steer the
    next attempt away from a path that actually works — the same invariant the
    user-facing floor already enforces."""
    state = _State(failures=("web_fetch",), recovered=("web_fetch",))
    assert "did not resolve it" not in describe_attempt_evidence(state)


def test_an_attempt_that_touched_nothing_says_nothing() -> None:
    """Empty, so the caller keeps its own generic reason rather than emitting a
    confident sentence about a turn that never ran a tool."""
    assert describe_attempt_evidence(_State()) == ""


def test_a_broken_state_costs_the_retry_nothing() -> None:
    """The expensive direction: summarising the last attempt must never be able
    to take the next one down."""

    class _Broken:
        trace_id = "t-2"

        @property
        def has_consequential_snapshot(self) -> bool:
            raise RuntimeError("ledger is gone")

    assert describe_attempt_evidence(_Broken()) == ""
    assert failed_capabilities_for_state(_Broken()) == []


# --------------------------------------------------------------------------- #
# The ban lands on what actually failed                                        #
# --------------------------------------------------------------------------- #


def test_the_ban_names_what_failed_not_what_was_touched_first() -> None:
    """The measured defect: send_message banned over a sequence that never
    contained it."""
    state = _State(failures=("web_fetch",), successes=("search_files", "read_file"))
    assert failed_capabilities_for_state(state) == ["web_fetch"]


def test_a_recovered_capability_is_never_banned() -> None:
    state = _State(failures=("web_fetch", "shell"), recovered=("web_fetch",))
    assert failed_capabilities_for_state(state) == ["shell"]


def test_no_recorded_failure_bans_nothing() -> None:
    """Better to learn nothing than to ban an innocent capability — a wrong ban
    closes a working path for every later attempt."""
    assert failed_capabilities_for_state(_State(successes=("read_file",))) == []


# --------------------------------------------------------------------------- #
# The reason survives the trip to the next attempt                             #
# --------------------------------------------------------------------------- #


def test_the_outcome_carries_the_reason() -> None:
    """Without this the durable runner mints its own text and the work's reason
    never reaches the task row that the next attempt is built from."""
    assert RetryOutcome(status="pending").reason == ""
    assert RetryOutcome(status="pending", reason="the last attempt floored: it "
                        "tried web_fetch").reason.startswith("the last attempt")


def test_the_durable_runner_prefers_the_works_reason() -> None:
    """The second seam. The runner had the outcome in hand and described the
    failure in its OWN vocabulary, overwriting everything below it."""
    import inspect

    from stackowl.pipeline.durable import task_loop_runner

    source = inspect.getsource(task_loop_runner)
    assert 'getattr(outcome, "reason", "")' in source, (
        "the runner is not reading the outcome's reason — every task's "
        "last_error goes back to 'retry did not deliver (actuator reported ...)'"
    )
    assert source.index('getattr(outcome, "reason", "")') < source.index(
        "retry did not deliver (actuator reported"
    ), "the machinery's wording must be the FALLBACK, not the value"


def test_the_actuator_prefers_the_evidence_over_the_tautology() -> None:
    """Structural, over the source: the literal 'retry attempt still floored'
    may survive only as a fallback."""
    import inspect

    from stackowl.pipeline import retry_actuator

    source = inspect.getsource(retry_actuator)
    assert "describe_attempt_evidence(final_state) or" in source, (
        "the floored-retry path no longer carries the turn's evidence — this is "
        "the exact regression that produced retries 2-6 with identical text"
    )
