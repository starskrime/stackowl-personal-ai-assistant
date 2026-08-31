"""A retry's augmented prompt must never be filed as something the user said.

MEASURED 2026-08-31 on the live database: of 368 ``staged_facts``, **145 (39%)**
contain retry/RCA/diagnostic markers — rows whose content reads::

    "User: (Retry attempt 2. What happened last time: the turn was BLOCKED: ..."

That is almost exactly the 37.1% of the 107,576-fact corpus that migration 0112
deleted for being "the platform's own diagnostics stored as durable memory ABOUT
THE USER". The same shape is accumulating again in the store that replaced it.

BOTH EXISTING GATES MISS IT, and neither is at fault:

* ``is_machine_lane`` is a prefix check on ``("goal-", "incident-")``. A retry
  REUSES the original human session key — ``owl:secretary:telegram:dm:...`` — so
  it is not a machine lane and never was.
* ``input_is_synthetic`` is set in exactly ONE place, ``orchestrator.py:2434``,
  from ``_prompt_depth > 0``. That catches text a COMMAND builder produced. The
  RetryActuator is not a command builder, so the flag stays False.

``state.py`` already records this exact failure for a different builder: four rows
reading ``'User: [/skill use] ...'`` were staged as things Bakir said, and the fix
was this flag. The RetryActuator writes machine text into ``input_text`` the same
way and never sets it.

THE FIX USES THE GATE THAT EXISTS rather than adding a third. The actuator KNOWS
its input is synthetic — it composed it — so it says so. No keyword list, no
content sniffing, no new predicate that could drift out of step with the builders.
"""

from __future__ import annotations

import inspect

from stackowl.pipeline import retry_actuator
from stackowl.pipeline.turn_persist import _is_not_a_user_utterance


class _State:
    """Minimal stand-in carrying only what the predicate reads."""

    def __init__(self, *, synthetic: bool, session_key: str) -> None:
        self.input_is_synthetic = synthetic
        self.session_key = session_key


def test_a_retry_on_a_human_lane_is_not_a_user_utterance() -> None:
    """The exact live shape: a retry keeps the user's Telegram session key."""
    state = _State(synthetic=True, session_key="owl:secretary:telegram:dm:72055773")
    assert _is_not_a_user_utterance(state) is True


def test_without_the_flag_a_retry_looks_like_the_user_talking() -> None:
    """Why the flag is required and the lane check cannot substitute for it."""
    state = _State(synthetic=False, session_key="owl:secretary:telegram:dm:72055773")
    assert _is_not_a_user_utterance(state) is False, (
        "a retry on a human lane is indistinguishable from a real user turn "
        "without input_is_synthetic — this is how 39% of staged_facts became "
        "'User: (Retry attempt 2...'"
    )


def test_a_real_user_turn_is_still_staged() -> None:
    """The expensive direction. A wrong True here silently drops real memories."""
    state = _State(synthetic=False, session_key="owl:secretary:telegram:dm:72055773")
    assert _is_not_a_user_utterance(state) is False


def test_every_state_the_retry_actuator_builds_declares_itself_synthetic() -> None:
    """Structural, over the source, so a THIRD construction site cannot be added
    without tripping this.

    Asserting on the source rather than by driving the actuator because both call
    sites need a live DbPool, a provider and a backend; the property being pinned
    is "the builder always marks its own output", which is visible where it is
    written.
    """
    source = inspect.getsource(retry_actuator)
    constructions = source.count("PipelineState(")
    marks = source.count("input_is_synthetic=True")
    assert constructions > 0, "the actuator no longer builds states — update this test"
    assert marks == constructions, (
        f"{constructions} PipelineState constructions but only {marks} marked "
        f"synthetic — an unmarked one is filed as a user utterance"
    )
