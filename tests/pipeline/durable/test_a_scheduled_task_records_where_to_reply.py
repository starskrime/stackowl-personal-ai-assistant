"""A task's destination must carry the ADDRESS, not just the channel name.

MEASURED IN PRODUCTION 2026-08-28, after Bakir reported "there is some job which
send me messages continuously". Ten jobmarket tasks were cycling: each drive
finished, delivery declined, the task therefore never counted as complete, the
liveness sweep reclaimed it as stale, and the retry ran the whole step again —
roughly every four minutes, for hours.

THE DELIVER STEP SAID EXACTLY WHY, in its own warning fields::

    [deliver] stream-miss: no durable fallback available
    {"has_deliverer": true, "has_target": false, "body_len": 136,
     "request_id": "recover-task-c85d535"}

An answer existed (136 bytes) and a deliverer existed. There was no TARGET.

``reply_target_for_task`` reads ``destination`` and its docstring states the
contract — "telegram:72055773" — returning None when there is no ``":"``. Every
one of the ten rows had ``destination = 'telegram'``: the channel with no
address. No target, so no delivery; no delivery, so no completion; no completion,
so retry for ever.

TWO WRITERS, ONE RULE, AND ONLY ONE OF THEM RIGHT. ``turn_task.py`` builds it
with ``_destination(channel, chat_id)`` and produces "telegram:72055773".
``task_runner.py`` wrote ``state.channel or None`` and produced "telegram".

The irony is that the comment directly above the bug describes the defect it was
introduced to fix: "Without these the rows existed but carried no destination and
no achievement condition, so they reached status='completed' with delivered_at
NULL — success claimed with no proof anyone received it." That fix cured the
false COMPLETION and created a never-completion in its place, because a channel
name is not an address.

This is the same shape as the 2026-08-19 consent defect, where a ConsentRequest
carried an identity and no address and every owl creation was refused.
"""

from __future__ import annotations

from stackowl.pipeline.durable.recovery import reply_target_for_task
from stackowl.pipeline.durable.turn_task import _destination


class _Task:
    def __init__(self, destination: str | None) -> None:
        self.destination = destination


def test_a_channel_name_alone_is_not_a_deliverable_destination() -> None:
    """THE regression, stated as the round trip that actually failed.

    'telegram' yields no target, which is what stopped ten tasks completing.
    """
    assert reply_target_for_task(_Task("telegram")) is None

    addressed = _destination("telegram", 72055773)
    assert reply_target_for_task(_Task(addressed)) == "72055773", (
        "a destination built by the shared helper must survive the round trip "
        "back to an address, or the answer has nowhere to go"
    )


def test_both_writers_use_the_same_helper() -> None:
    """One rule, one source — asserted by BEHAVIOUR, not by grepping source text.

    task_runner.py built the destination by hand and got it wrong while
    turn_task.py's helper got it right. Any future writer must ask a shared helper
    rather than keep a second opinion about what a destination looks like.

    THIS TEST USED TO GREP THE FILE for the literal `_destination(`, and it went red
    on 2026-08-29 when task_runner moved to `destination_for_turn(` — which IS the
    shared helper and calls `_destination` internally. The invariant held; the
    expression of it did not. A guard that fires on a rename it should not care
    about, and would sail past a hand-rolled f-string it SHOULD care about, is
    testing the wrong thing.

    So assert the property itself: both writers, given the same inputs, agree on
    the shape they produce. That survives renames and catches divergence.
    """
    from stackowl.pipeline.durable.turn_task import (
        _destination,
        destination_for_turn,
    )

    # The two writers serve different cases — enqueue_turn_task handles CHAT turns
    # which always deliver; task_runner handles turns that may DEFER — so they are
    # allowed to differ on the deferred case, and must agree on every other.
    for channel, chat_id in (
        ("telegram", 72055773), ("telegram", "72055773"),
        ("slack", "C123ABC"), ("whatsapp", "+15551234"), ("cli", None),
    ):
        assert destination_for_turn(
            channel=channel, reply_target=chat_id, defer_delivery=False,
        ) == _destination(channel, chat_id), (
            f"the two writers disagree about {channel}/{chat_id!r} — that "
            "divergence is the bug this guard exists to prevent"
        )


def test_task_runner_does_not_hand_build_a_destination() -> None:
    """The thing the old grep was actually reaching for.

    A future edit that assembles `destination=f"{channel}:{target}"` inline would
    re-create the exact defect (a channel NAME written where an ADDRESS belongs),
    and no behavioural test can see a string that never reaches a helper. This is
    the one case where reading the source is the right instrument — so it checks
    for hand-assembly rather than for a particular function name.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    runner = (root / "src" / "stackowl" / "pipeline" / "durable"
              / "task_runner.py").read_text(encoding="utf-8")

    assert "destination=state.channel or None" not in runner, (
        "task_runner is writing a bare channel name as the destination again — "
        "reply_target_for_task will return None and the task will never complete"
    )
    assert 'destination=f"' not in runner, (
        "task_runner is assembling a destination string by hand again"
    )
    assert "from stackowl.pipeline.durable.turn_task import" in runner, (
        "task_runner must get its destination builder from turn_task"
    )


def test_a_channel_with_no_address_is_still_honest() -> None:
    """The control. CLI addresses its one terminal implicitly.

    None here is CORRECT and must not be 'fixed' by inventing a recipient — the
    helper's docstring says so, and a sweep has nobody waiting.
    """
    assert _destination("cli", None) == "cli"
    assert reply_target_for_task(_Task("cli")) is None
    assert reply_target_for_task(_Task(None)) is None
