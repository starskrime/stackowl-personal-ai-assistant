"""What a turn remembered was unobservable, so no claim about it could be checked.

Bakir, 2026-09-09: "Agent context usage not correct … today's context switching,
compaction, memory handling, long term memory does not work correctly."

THAT REPORT COULD BE NEITHER CONFIRMED NOR REFUTED. MEASURED: `_gather_history` logs
only its ERROR path, and the only line in the whole pipeline carrying a history turn
count is `classify: conversation too long`, which fires exclusively when compression
runs — and since DEBT-248 raised the budget from a fixed 12,000 to 0.75 of the resolved
window (196,608 here), compression essentially never runs. So on a normal turn NOTHING
recorded how much history was assembled or which key it was read under.

WHAT THAT COST, concretely: my own DEBT-251 record claims a recovery turn "assembles
history from a different bucket than the chat it delivers into". That claim is
plausible — recovery turns carry `owl:*:recovery:*`, a MACHINE LANE
(`sessions/models.py:288`, "a lane with no person on it") while ordinary turns file
under `owner_scope_key` — and it was UNVERIFIABLE when written. A claim nobody can
check is not a finding; this is the premise being fixed rather than argued.

`scope_key` is the field that makes the line diagnostic rather than decorative: a turn
reading a THIN history and a turn reading the WRONG BUCKET look identical without it.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.pipeline.steps import classify


@pytest.mark.tripwire
def test_every_turn_records_what_history_it_assembled() -> None:
    """WIRED, AND NOT ONLY WHEN COMPRESSION FIRES.

    The pre-existing turn count lived inside `_compress_history`'s early-return
    branch, so it was silent on the overwhelming majority of turns. This one sits on
    the main path of `run`.
    """
    src = inspect.getsource(classify.run)

    assert "classify: history assembled" in src, (
        "history assembly is unobservable again — no claim about context can be checked"
    )
    for field in ("scope_key", "read_turns", "tokens"):
        assert f'"{field}"' in src, f"the line no longer carries {field}"


@pytest.mark.tripwire
def test_it_is_INFO_because_production_runs_at_INFO() -> None:
    """A DEBUG line does not exist when the question is asked — this repo has paid for
    that once already, with an acceptance check whose only evidence was below the
    production level."""
    src = inspect.getsource(classify.run)
    idx = src.index("classify: history assembled")

    # The nearest `log.*` call BEFORE the message is the one that emits it. Asserting
    # the text immediately preceding was brittle — the message sits on the line after
    # the call, so the character before it is a quote, and this test failed on its own
    # correct subject the first time it ran.
    calls = [c for c in ("log.engine.info(", "log.engine.debug(", "log.engine.warning(",
                         "log.engine.error(") if c in src[:idx]]
    nearest = max(calls, key=lambda c: src[:idx].rindex(c))

    assert nearest == "log.engine.info(", (
        f"the history line is emitted by {nearest} — production runs at INFO, so a "
        f"DEBUG line does not exist when the question is asked"
    )


def test_the_scope_key_is_the_one_history_was_actually_read_under() -> None:
    """The field must be the SAME value passed to `_gather_history`, not a re-derivation.

    Two calls to `owner_scope_key(state)` could drift; reporting a key the read did not
    use would be worse than reporting none, because it would look authoritative.
    """
    src = inspect.getsource(classify.run)

    assert "_scope = owner_scope_key(state)" in src
    assert "_gather_history(_scope," in src, "the read no longer uses the reported key"
    assert '"scope_key": _scope' in src, "the reported key is re-derived, not the one used"


def test_the_read_depth_and_the_kept_count_are_both_reported() -> None:
    """`read_turns` vs `turns` is the whole diagnostic: a turn that READ 40 and KEPT 9
    was compressed; a turn that read 2 has a thin history; a turn that read 0 under an
    unexpected scope_key read the wrong bucket. One number cannot say which."""
    src = inspect.getsource(classify.run)

    assert "_read_turns = len(history)" in src
    assert '"turns": len(history)' in src
    assert '"compressed": len(history) != _read_turns' in src
