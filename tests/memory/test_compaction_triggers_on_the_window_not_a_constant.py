"""Compaction fired at 4.6% of the window and cost more than it saved.

Bakir, 2026-09-09, on a live Telegram turn: he sent "hi" and waited ~2.5 minutes.
His diagnosis, and it was better than mine: *"compaction should br when it is 200k
not on 12 k"*.

WHAT THE TURN COST. One call dominates it — 12,156 in / 9,236 out in 139,834 ms,
91% of the turn's model time — the history summarizer. `HISTORY_BUDGET_TOKENS` was
a fixed 12,000, which is **4.6%** of this deployment's 262,144 window.

AND THE FIXED NUMBER COULD NOT SEE THE TRADE IT WAS MAKING. MEASURED over 2,279
live calls: generation runs at **15 ms/output-token** and prefill at
**0.138 ms/input-token** — a 109:1 ratio. Compacting spent ~139 s of generation to
avoid sending ~53,600 tokens of history, which is ~7.4 s of prefill. **It paid 139
seconds to save 7.** A trigger that is a token count cannot weigh that, because the
decision it gates is a COMPARISON and a constant only has one side of it.

`DEEP_HISTORY_TURNS` already caps the READ at 40 turns, so history is structurally
bounded at ~46,640 tokens — 17.8% of this window — whatever the budget says. The
read cap was already doing the protecting; the budget only made compaction fire
early, MEASURED at turn ~10.

WHY IT HAD TO BE WIRED, NOT JUST RE-NUMBERED. `state.model_window` is filled by
`assemble`, which runs AFTER `classify`. Reading it in the compressor would have
been None on every turn and the window-relative budget would have been decoration
that never engaged — the shape this repo names most often. `resolve_turn_window`
now exists once and both steps ask it.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.memory import conversation_compressor as cc
from stackowl.providers.base import Message

_WINDOW = 262_144
#: Measured 2026-09-01 and still the live figure: history runs ~1,166 tokens a turn.
_PER_TURN = 1_166


def _history(turns: int) -> list[Message]:
    """`turns` messages of roughly the measured live size."""
    body = "x " * (_PER_TURN * 2)  # estimate_tokens is ~chars/4; "x " is 2 chars
    return [Message(role="user" if i % 2 == 0 else "assistant", content=body)
            for i in range(turns)]


@pytest.mark.tripwire
def test_the_budget_is_a_share_of_the_window_he_asked_for() -> None:
    """200k, expressed as a property of the model rather than a second constant."""
    assert cc.history_budget(_WINDOW) == 196_608
    assert 190_000 < cc.history_budget(_WINDOW) < 210_000, "not the ~200k asked for"


@pytest.mark.tripwire
def test_an_unknown_window_keeps_the_old_conservative_budget() -> None:
    """The probe cache is empty at rest, so the first turn after a boot cannot
    resolve a window. That must degrade to today's behaviour, never to unbounded."""
    for unknown in (None, 0, -1):
        assert cc.history_budget(unknown) == cc.HISTORY_BUDGET_TOKENS == 12_000


@pytest.mark.tripwire
def test_a_small_window_model_is_still_protected() -> None:
    """The point is not "compact less" — it is "compact against the real window".
    A lean model gets a SMALLER budget than the old constant, not a larger one."""
    assert cc.history_budget(8_192) == 6_144
    assert cc.history_budget(8_192) < cc.HISTORY_BUDGET_TOKENS


def test_his_conversation_no_longer_compacts_and_the_old_budget_did() -> None:
    """THE REGRESSION THAT MATTERS, at the size that actually bit him.

    46 turns is the history his "hi" carried. Against the window it fits; against
    the old fixed 12,000 it did not, which is the 139-second call.
    """
    history = _history(46)
    assert cc.plan(history, window=_WINDOW).needs_compression is False, (
        "46 turns still compacts against a 262,144 window — the trigger is not "
        "window-relative"
    )
    assert cc.plan(history, window=None).needs_compression is True, (
        "the fixed-budget path no longer compacts either — this test can no longer "
        "tell the fix from a compressor that never runs"
    )


def test_a_conversation_that_genuinely_threatens_the_window_still_compacts() -> None:
    """Not a licence to never compact. Past the share, it must still fire."""
    huge = _history(400)
    assert cc.plan(huge, window=_WINDOW).needs_compression is True


@pytest.mark.tripwire
def test_classify_resolves_the_window_rather_than_reading_a_field_set_later() -> None:
    """WIRED, NOT DECORATION.

    `assemble` fills `state.model_window` and runs AFTER `classify`, so a compressor
    that only read the field would get None every turn and silently keep the old
    budget. This asserts the call site actually resolves one.
    """
    from stackowl.pipeline.steps import classify

    src = inspect.getsource(classify._compress_history)  # noqa: SLF001
    assert "resolve_turn_window" in src, (
        "classify no longer resolves a window — the window-relative budget is "
        "decoration again"
    )
    assert "cc.plan(history, window=" in src, "the window is resolved but not passed"


@pytest.mark.tripwire
def test_the_window_resolver_has_exactly_one_copy() -> None:
    """`assemble` and `classify` need the same answer. Two copies of one rule is
    the shape this repo pays for most — `_safe_resolve_api_key` was already
    duplicated across `assemble.py` and `execute.py` and is the standing example."""
    from stackowl.pipeline import provider_select
    from stackowl.pipeline.steps import assemble

    assert hasattr(provider_select, "resolve_turn_window")
    assert "resolve_turn_window" in inspect.getsource(assemble.run), (
        "assemble stopped asking the shared resolver — it has its own copy again"
    )
