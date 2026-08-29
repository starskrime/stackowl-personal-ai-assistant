"""Text a COMMAND authored is never filed as something the user said.

I CLAIMED THIS WAS DISSOLVED AND IT WAS NOT. D10.5's design argued that steering
(telling the turn to load a skill) instead of injecting (pasting the skill body)
removed the durable-user-fact hazard outright, and that
``PipelineState.input_is_synthetic`` was therefore unnecessary. That was half
right and the wrong half was shipped.

MEASURED on the live table during D10.5's own validate — four rows::

    staged_facts.content = 'User: [/skill use] The user has invoked the skill
                            "verify-before-claim" and wants you to follow it. ...'

The BODY does stay a tool result, as designed. But the turn PROMPT is
``state.input_text``, and ``turn_persist`` stages ``"User: {input_text}"``. So the
directive the command wrote is filed as a durable user utterance, embedded, and
offered back later as something Bakir said.

It is the 4,480-of-5,212 machine-prompt defect again, one door further along, and
``is_machine_lane`` cannot see it: that guard is a PREFIX CHECK on
``("goal-", "incident-")`` and this arrives on ``owl:secretary:telegram:dm:...``.

THE PREDICATE IS STRUCTURAL, NOT LEXICAL. ``_prompt_depth > 0`` means the gateway
re-dispatched this message with text a command produced — the exact condition, with
no tag-matching or keyword list to drift. The transcript is still written: the turn
happened and stays inspectable; what is refused is filing it as knowledge.
"""

from __future__ import annotations

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(*, synthetic: bool) -> PipelineState:
    return PipelineState(
        trace_id="t-syn",
        session_key="owl:secretary:telegram:dm:72055773",
        conversation_id="c-1",
        input_text='[/skill use] The user has invoked the skill "verify-before-claim"...',
        channel="telegram",
        owl_name="secretary",
        pipeline_step="deliver",
        input_is_synthetic=synthetic,
        responses=(
            ResponseChunk(
                content="done", is_final=True, chunk_index=0,
                trace_id="t-syn", owl_name="secretary",
            ),
        ),
    )


def test_the_flag_exists_and_defaults_to_false() -> None:
    """Defaulted, so every existing construction site is unaffected."""
    assert _state(synthetic=False).input_is_synthetic is False
    assert _state(synthetic=True).input_is_synthetic is True


def test_a_synthetic_turn_is_refused_as_a_user_fact() -> None:
    """The bug: a command's own directive filed as a user utterance."""
    from stackowl.pipeline.turn_persist import _is_not_a_user_utterance

    assert _is_not_a_user_utterance(_state(synthetic=True)) is True


def test_an_ordinary_turn_on_the_SAME_LANE_is_still_staged() -> None:
    """The guard must be narrow.

    This is a normal Telegram lane. If the predicate keyed on the lane it would
    stop the platform learning anything Bakir actually says — a far worse bug in
    the opposite direction. It keys on WHO WROTE THE TEXT, not on where it arrived.
    """
    from stackowl.pipeline.turn_persist import _is_not_a_user_utterance

    assert _is_not_a_user_utterance(_state(synthetic=False)) is False


def test_a_machine_lane_is_still_refused() -> None:
    """The 2026-08-25 guard must survive — this ADDS a case, it does not replace one."""
    from stackowl.pipeline.turn_persist import _is_not_a_user_utterance

    machine = _state(synthetic=False).evolve(session_key="incident-abc123")
    assert _is_not_a_user_utterance(machine) is True
