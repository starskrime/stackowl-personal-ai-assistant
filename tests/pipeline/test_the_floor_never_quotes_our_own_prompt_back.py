"""The give-up floor may quote the USER's goal back. Never our own prompt.

WHAT HE ACTUALLY RECEIVED ON TELEGRAM, verbatim from the live archive:

    I couldn't fully complete this: Today's date is 2026-09-03. This is a
    recurring scheduled check running right now — fetch current, up-to-date
    information...

    I couldn't fully complete this: (Retry attempt 2. What happened last time:
    execute: CircuitOpenError: Circuit open for 'NeraAiRaw' — retry after 0s)...

    I couldn't fully complete this: You are the VERIFIER owl in a fixed-stage
    incident root-cause analysis. Your ONLY job is to check whether the
    hypothes...

MEASURED 2026-09-03 over every Telegram turn whose reply opens with that
sentence — 1,547 of them:

    scheduled job prompt      1,287   83%
    retry scaffolding            54    3%
    owl role prompt              37    2%
    ------------------------------------
    OUR OWN TEXT              1,378   89%
    his own words               169   11%   <- the only correct case

Nine times out of ten the "goal" he is told we could not complete is a sentence
we wrote to ourselves. He never asked it. A scheduled job's preamble, a retry's
bookkeeping, and an owl's role instructions are all read back to him as though
they were his request.

THE CAUSE IS ONE FIELD CARRYING TWO MEANINGS. Six floor call sites pass
``goal=state.input_text``. ``input_text`` is not "what the user asked" — it is
"the prompt this turn ran", whoever composed it. For an interactive turn those
coincide; for a scheduled job, a retry replay, or a machine lane they do not,
and nothing at the floor distinguishes them.

THE JUDGEMENT ALREADY EXISTS, one layer over. ``turn_persist`` faced the exact
same question — may this text be stored as something Bakir said? — and answered
it structurally with ``input_is_synthetic or is_machine_lane(session_key)``,
after 4,480 of 5,212 staged rows turned out to be the platform's own prompts.
That predicate was private to the persistence module, so the floor re-derived
nothing and quoted the prompt. One rule, and the second copy of it was missing
rather than wrong.

NOT ESC-14, AND DELIBERATELY NOT A SECOND ATTEMPT AT IT. ESC-14 (the volatile
turn context is CONCATENATED onto the user's message) was decided by Bakir on
2026-08-15 and absorbed into D04.1, with ``strip_turn_context`` kept as a net
until then. That is a different fault: it pollutes text the user DID write. The
1,378 turns above have no user text to pollute — the whole input is ours, and
stripping a leading clock sentence cannot help. Both are left exactly as he
decided; this fixes the case his decision does not cover.

WHY A PREDICATE AND NOT A PATTERN. Recognising "this looks like a system prompt"
would need a keyword list, which is banned here and would go stale the first time
a new prompt builder is added — which is precisely how this arrived. Authorship
is a structural fact the platform already records; the floor asks for it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from stackowl.pipeline.state import PipelineState, user_goal

#: The three shapes measured above, verbatim heads of live rows.
SCHEDULED = (
    "Today's date is 2026-09-03. This is a recurring scheduled check running "
    "right now — fetch current, up-to-date information."
)
RETRY = (
    "(Retry attempt 2. What happened last time: execute: CircuitOpenError: "
    "Circuit open for 'NeraAiRaw' — retry after 0s)"
)
OWL_ROLE = (
    "You are the VERIFIER owl in a fixed-stage incident root-cause analysis. "
    "Your ONLY job is to check whether the hypothesis holds."
)
HIS_WORDS = (
    "Create a agent which will check job market near me . I am on zip code 75025."
)


def _state(text: str, *, synthetic: bool = False, session_key: str = "tg-123") -> PipelineState:
    return PipelineState(
        trace_id="t", session_key=session_key, input_text=text, channel="telegram",
        owl_name="secretary", pipeline_step="start", input_is_synthetic=synthetic,
    )


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "synthetic", "session_key"),
    [
        (SCHEDULED, True, "goal-goal_execution-7b6da65e"),
        (RETRY, True, "owl:secretary:telegram:dm:abc"),
        (OWL_ROLE, False, "incident-b5543c489cae"),
    ],
    ids=["scheduled-job", "retry-scaffolding", "owl-role"],
)
def test_our_own_prompt_is_never_offered_as_his_goal(
    text: str, synthetic: bool, session_key: str,
) -> None:
    """THE DEFECT, in its three measured shapes. 1,378 Telegram replies opened by
    quoting one of these back at him as the thing he had asked for."""
    assert user_goal(_state(text, synthetic=synthetic, session_key=session_key)) is None, (
        "the floor was handed our own prompt as the user's goal"
    )


def test_his_own_words_are_still_quoted_back() -> None:
    """THE OTHER DIRECTION, and the reason this is not "never show a goal". The
    169 correct cases are the whole value of the sentence: naming what he asked
    is how he knows the failure was about HIS request."""
    assert user_goal(_state(HIS_WORDS)) == HIS_WORDS


def test_a_machine_lane_is_caught_even_when_the_flag_is_unset() -> None:
    """The two signals are not redundant. A machine lane sets no synthetic flag
    (the owl-role rows above carry ``input_is_synthetic=False``), and synthetic
    input arrives on ORDINARY lanes where the prefix check is blind. Either alone
    would leave one of the measured populations leaking."""
    assert user_goal(_state(OWL_ROLE, synthetic=False, session_key="incident-abc")) is None
    assert user_goal(_state(SCHEDULED, synthetic=True, session_key="tg-999")) is None


def test_the_turn_context_strip_is_still_applied_to_his_words() -> None:
    """ESC-14's net stays under the case it covers — text he DID write, with our
    clock sentence prepended. Bakir decided that fix belongs to D04.1; removing
    the net here would quietly undo his decision."""
    composed = "Right now it is Saturday, August 15, 2026 at 04:10 PM CDT.\n\n" + HIS_WORDS
    got = user_goal(_state(composed))
    assert got == HIS_WORDS, got


def test_an_empty_input_yields_no_goal_rather_than_an_empty_quote() -> None:
    """``strip_turn_context`` can reduce a composed input to "", and the floor's
    goal-bearing sentence would then render "I couldn't fully complete this: ."
    — a defect the localization file already carries a comment about."""
    assert user_goal(_state("")) is None
    assert user_goal(_state("   ")) is None


# --------------------------------------------------------------------------- #
# One source, and it must stay one                                             #
# --------------------------------------------------------------------------- #


def test_the_persistence_layer_asks_the_same_predicate() -> None:
    """``turn_persist`` answered this question first. If it kept a private copy,
    the two would drift — and the drift would be silent, because each looks
    correct on its own."""
    from stackowl.pipeline import turn_persist

    assert turn_persist._is_not_a_user_utterance is not None
    st = _state(OWL_ROLE, session_key="incident-abc")
    assert turn_persist._is_not_a_user_utterance(st) is True
    assert turn_persist._is_not_a_user_utterance(_state(HIS_WORDS)) is False


@pytest.mark.tripwire
def test_no_floor_call_site_passes_the_raw_input_text() -> None:
    """THE GUARD THAT MAKES THIS STICK. Six sites passed ``goal=state.input_text``
    and every one of them looked right in isolation — the defect only exists in
    the gap between "the prompt we ran" and "what he asked". A seventh site added
    later would reopen it silently, so the arrangement is asserted rather than
    trusted: no caller of ``synthesize_floor`` may pass the raw input as goal."""
    root = Path(__file__).resolve().parents[2] / "src" / "stackowl"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover — the syntax gate covers this
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name not in ("synthesize_floor", "synthesize_from_calls"):
                continue
            for kw in node.keywords:
                if kw.arg != "goal":
                    continue
                src = ast.unparse(kw.value)
                if "input_text" in src and "user_goal" not in src:
                    offenders.append(f"{path.relative_to(root.parent.parent)}:{node.lineno} -> {src}")
    assert not offenders, (
        "a floor call site passes the raw turn prompt as the user's goal:\n  "
        + "\n  ".join(offenders)
    )
