"""A retry must be told WHICH ceiling stopped it, and that it repeated itself.

WHY THIS EXISTS — the channel was fixed once and still went quiet.

``describe_attempt_evidence`` was written on 2026-08-31 to replace the tautology
``"retry attempt still floored"`` with the turn's own evidence. It worked:
measured 2026-09-03, useful retry feedback went from 19% before the fix to 67%
after. But 5 of the 15 post-fix retries STILL carried the tautology, and three of
those five had run 26, 23 and 15 tool calls. A richly-evidenced turn was
described as "still floored".

THE CAUSE. ``describe_attempt_evidence`` read the consequential snapshot, which
``tool_outcome_ledger`` filters to ``_EFFECTFUL = {"write","consequential"}`` --
correctly, because that snapshot answers a DIFFERENT question: "did an effect the
user was promised actually land?" The retry channel asks the opposite question:
"what did this turn burn itself on?" On a READ sweep the snapshot is empty by
construction, ``has_consequential_snapshot`` is still True (execute stamps the
flag), so the live-ledger fallback is never taken and the function returns "".

AND READ SWEEPS ARE THE DOMINANT FAILURE SHAPE. Measured over 14 days of
``failure_class='stop'`` outcomes: 60 of 79 contained a run of >=5 identical
consecutive calls. The worst was **46 consecutive ``web_fetch`` calls out of 48**.
One task reached attempt 6 having re-entered that identical path every time --
``DEFAULT_MAX_ATTEMPTS`` is 30.

WHAT THE NEXT ATTEMPT NEEDS, in priority order, because ``_augment_goal``
truncates the reason at ``_RETRY_REASON_CHARS`` (400) and a long tool list would
push the actionable half off the end:

1. WHICH CEILING fired. The remedy differs per cap and the model cannot infer it:
   ``steps`` -> fewer, larger calls; ``time`` -> avoid slow actuators; ``tokens``
   -> read less, stop re-reading. Measured 2026-09-02, the cap that fires MOVED
   from ``steps`` to ``tokens`` (steps 30/5/4/2/0 vs tokens 0/0/8/8/7 across
   08-28..09-02), so a hardcoded "ran out of steps" would now be false.
2. THAT IT REPEATED ITSELF, with the count. "It tried web_fetch" is equally true
   of a turn that succeeded; "39 of those calls were web_fetch back to back" is
   not.

THE BAN DOOR STAYS SHUT. ``_pick_newly_failed`` bans a capability, and a ban is a
HARD exclusion from the presented tool array, not advice. Its fallback iterated
``_attempts_for_state`` -- which unions failures with SUCCESSES -- so it could ban
a tool that worked. Measured baseline across every log file: 15 ban events, every
one of them write/consequential severity (memory, shell, browser_eval_js,
write_file, send_message, owl_build), zero read tools. Widening the EVIDENCE
sentence must never widen the BAN: banning ``web_fetch`` on a research task
blinds it of the tool the task exists to use.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.delivery_gate import describe_attempt_evidence
from stackowl.pipeline.retry_actuator import RetryActuator


class _Call:
    """Only the ToolCall fields the evidence sentence reads."""

    def __init__(self, tool_name: str, error: str | None = None) -> None:
        self.tool_name = tool_name
        self.error = error


class _State:
    """A floored turn, in the shape the retry channel actually receives."""

    def __init__(
        self,
        *,
        tool_calls: tuple[_Call, ...] = (),
        budget_capped: bool = False,
        budget_cap: str = "",
        failures: tuple[str, ...] = (),
        successes: tuple[str, ...] = (),
        recovered: tuple[str, ...] = (),
    ) -> None:
        self.tool_calls = tool_calls
        self.budget_capped = budget_capped
        self.budget_cap = budget_cap
        self.consequential_failures = failures
        self.consequential_successes = successes
        self.recovered_consequential = recovered
        self.errors: tuple[str, ...] = ()
        self.trace_id = "t-ceiling"

    @property
    def has_consequential_snapshot(self) -> bool:
        return True


def _read_sweep(n: int = 26, tool: str = "web_fetch") -> _State:
    """The measured shape: a long read-only sweep with an EMPTY snapshot."""
    return _State(
        tool_calls=tuple(_Call(tool) for _ in range(n)),
        budget_capped=True,
        budget_cap="tokens",
    )


# --------------------------------------------------------------------------- #
# The sentence exists at all on a read sweep                                   #
# --------------------------------------------------------------------------- #


def test_a_read_only_sweep_is_no_longer_described_as_still_floored() -> None:
    """THE REGRESSION THIS FILE EXISTS FOR. 26 read-severity calls, empty
    consequential snapshot -> the old code returned "" and the caller fell back
    to the tautology."""
    evidence = describe_attempt_evidence(_read_sweep())
    assert evidence != ""
    assert "web_fetch" in evidence


def test_the_ceiling_that_fired_is_named() -> None:
    """Not "it floored" -- WHICH cap. The remedy differs per cap."""
    assert "tokens" in describe_attempt_evidence(_read_sweep())


def test_the_ceiling_is_not_hardcoded_to_steps() -> None:
    """The cap that fires MOVED from steps to tokens on 2026-09-02. A sentence
    that says "steps" for a token breach is a confident false statement, and it
    would send the next attempt to batch its calls when it needed to read less."""
    steps = describe_attempt_evidence(
        _State(
            tool_calls=(_Call("shell"),), budget_capped=True, budget_cap="steps",
        )
    )
    assert "steps" in steps
    assert "tokens" not in steps


def test_the_repetition_is_counted_not_merely_listed() -> None:
    """"It tried web_fetch" is equally true of a turn that SUCCEEDED. The count
    of CONSECUTIVE calls is the part that says the path is exhausted.

    Asserts the whole clause, not the bare number: an earlier version of this
    test checked only ``"39" in evidence``, which the "it tried web_fetch x39"
    tally clause satisfies on its own — so raising the repeat threshold to a
    value that can never fire left the test green. Caught by mutation."""
    evidence = describe_attempt_evidence(_read_sweep(n=39))
    assert "39 of those calls were web_fetch back to back" in evidence


def test_a_varied_sweep_is_not_accused_of_repeating_itself() -> None:
    """The denominator check: a turn that ran 6 DIFFERENT tools did not spiral,
    and telling it that it did would be a fabricated diagnosis."""
    varied = _State(
        tool_calls=tuple(
            _Call(n) for n in
            ("read_file", "search_files", "web_search", "todo", "web_fetch", "shell")
        ),
        budget_capped=True,
        budget_cap="tokens",
    )
    evidence = describe_attempt_evidence(varied)
    assert "back to back" not in evidence


def test_the_cause_clause_comes_first() -> None:
    """Ordering is the invariant, and it must be asserted directly.

    An earlier version checked only ``"tokens" in evidence[:400]`` on a sentence
    that was 256 chars long — so the assertion could not fail however the clauses
    were reordered. Caught by mutation."""
    assert describe_attempt_evidence(_read_sweep()).startswith(
        "the last attempt ran out of tokens"
    )


def test_the_cause_survives_the_400_char_truncation() -> None:
    """``_augment_goal`` truncates the reason at _RETRY_REASON_CHARS. A sweep wide
    enough to overflow that budget must still lead with the ceiling name."""
    from stackowl.pipeline.retry_actuator import _RETRY_REASON_CHARS

    wide = _State(
        tool_calls=tuple(
            _Call(f"some_rather_long_tool_name_{i}", error="boom") for i in range(40)
        ),
        budget_capped=True,
        budget_cap="tokens",
    )
    evidence = describe_attempt_evidence(wide)
    assert len(evidence) > _RETRY_REASON_CHARS, (
        "this fixture no longer overflows the truncation budget, so it cannot "
        "prove the cause clause survives it"
    )
    assert "tokens" in evidence[:_RETRY_REASON_CHARS]


def test_an_attempt_that_ran_no_tool_still_says_nothing() -> None:
    """Two of the five measured tautologies were AllProvidersUnavailableError
    turns with tool_call_count=0. There is genuinely nothing to describe, and
    inventing a sentence for them would be the fabrication this whole channel
    was built to remove."""
    assert describe_attempt_evidence(_State()) == ""


def test_a_broken_state_still_costs_the_retry_nothing() -> None:
    """Summarising the last attempt must never take the next one down."""

    class _Broken:
        trace_id = "t-broken"

        @property
        def tool_calls(self) -> tuple[_Call, ...]:
            raise RuntimeError("state is gone")

    assert describe_attempt_evidence(_Broken()) == ""


# --------------------------------------------------------------------------- #
# The ban door stays shut -- the invariant, not the sentence                   #
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_a_read_only_sweep_never_bans_a_capability() -> None:
    """A ban REMOVES the tool from the presented array (execute._restrict_to_for_turn).
    Banning web_fetch on a research task blinds it of the tool the task exists to
    use, and nothing raises when it happens -- the turn just quietly has fewer
    tools. Measured baseline: 15 ban events across every log file, all
    write/consequential, zero read tools."""

    class _Row:
        banned_capabilities: tuple[str, ...] = ()

    picked = RetryActuator._pick_newly_failed(  # type: ignore[arg-type]
        None, _Row(), _read_sweep(),
    )
    assert picked == "", (
        f"a read-only sweep produced a ban on {picked!r} — the evidence sentence "
        "was widened into the ban channel"
    )


@pytest.mark.tripwire
def test_the_ban_never_reaches_for_the_attempted_list() -> None:
    """RETIRED MEANS DELETED. ``_pick_newly_failed`` used to fall back to
    ``_attempts_for_state``, which unions failures with SUCCESSES -- so it could
    ban a tool that worked. The primary source (``failed_capabilities_for_state``)
    is the correct one; the fallback is a positional guess and is deleted, not
    guarded."""
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(RetryActuator._pick_newly_failed))
    fn = ast.parse(source).body[0]
    assert isinstance(fn, ast.FunctionDef)
    # The DOCSTRING names it deliberately, to record why it was removed. Strip the
    # prose and read only what executes — a source grep would pass or fail on the
    # comment rather than on the code.
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    called = {
        n.func.id for n in ast.walk(ast.Module(body=body, type_ignores=[]))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_attempts_for_state" not in called, (
        "the ban path reaches for the attempted list again — that list unions "
        "consequential_failures with SUCCESSES, so it can ban a tool that worked"
    )


@pytest.mark.tripwire
def test_a_real_consequential_failure_is_still_banned() -> None:
    """The other direction: deleting the fallback must not disarm the ban for the
    write-severity failures that the 15 measured ban events are made of."""

    class _Row:
        banned_capabilities: tuple[str, ...] = ()

    state = _State(
        tool_calls=(_Call("shell", error="boom"),),
        failures=("shell",),
        budget_capped=True,
        budget_cap="steps",
    )
    picked = RetryActuator._pick_newly_failed(  # type: ignore[arg-type]
        None, _Row(), state,
    )
    assert picked == "shell"


# --------------------------------------------------------------------------- #
# A turn that never got to act still knows WHY                                 #
# --------------------------------------------------------------------------- #


def test_a_turn_that_ran_no_tool_still_reports_its_error() -> None:
    """MY OWN REASONING, CORRECTED. This file originally asserted that a turn
    with zero tool calls "says nothing", on the grounds that two of the five
    measured tautologies were provider-unavailable turns with tool_call_count=0
    and "there is genuinely nothing to describe".

    That was wrong, and the live outage on 2026-09-03 proved it. When the model
    provider is unreachable the turn cannot run a single tool — so tool_calls is
    empty, describe_attempt_evidence returned "", and the actuator fell back to
    "retry attempt still floored" while the platform knew perfectly well the
    cause was AllProvidersUnavailableError. MEASURED: 16 failed turns in the
    first hours after that restart ran ZERO tools and carried a known
    failure_class; the one actuator evidence line emitted in that window was the
    tautology.

    Same shape as the floor fix shipped hours earlier: the cause was measured,
    classified, and then discarded in favour of a generic sentence. An attempt
    that never got to act has the MOST to say about why."""
    state = _State(budget_capped=False)
    state.errors = (
        "execute: AllProvidersUnavailableError: All providers unavailable: "
        "NeraAiRaw: skipped (circuit open)",
    )
    evidence = describe_attempt_evidence(state)
    assert evidence, "a turn that failed before acting reported nothing"
    assert "AllProvidersUnavailableError" in evidence
    assert "still floored" not in evidence


def test_the_error_is_bounded_so_it_cannot_eat_the_reason_budget() -> None:
    """_augment_goal truncates at _RETRY_REASON_CHARS. A provider stack trace
    pasted whole would push everything else off the end."""
    from stackowl.pipeline.retry_actuator import _RETRY_REASON_CHARS

    state = _State(budget_capped=False)
    state.errors = ("execute: " + "x" * 4000,)
    assert len(describe_attempt_evidence(state)) < _RETRY_REASON_CHARS


def test_a_turn_with_neither_tools_nor_errors_still_says_nothing() -> None:
    """The genuine empty case survives: no tools, no error, nothing to claim."""
    state = _State(budget_capped=False)
    state.errors = ()
    assert describe_attempt_evidence(state) == ""
