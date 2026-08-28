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
    """One rule, one source — the property that stops them diverging again.

    task_runner.py built the destination by hand and got it wrong while
    turn_task.py's helper got it right. Any future writer must ask the helper
    rather than keep a second opinion about what a destination looks like.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    runner = (root / "src" / "stackowl" / "pipeline" / "durable"
              / "task_runner.py").read_text(encoding="utf-8")

    assert "destination=state.channel or None" not in runner, (
        "task_runner is writing a bare channel name as the destination again — "
        "reply_target_for_task will return None and the task will never complete"
    )
    assert "_destination(" in runner, (
        "task_runner must build the destination through the shared helper"
    )


def test_a_channel_with_no_address_is_still_honest() -> None:
    """The control. CLI addresses its one terminal implicitly.

    None here is CORRECT and must not be 'fixed' by inventing a recipient — the
    helper's docstring says so, and a sweep has nobody waiting.
    """
    assert _destination("cli", None) == "cli"
    assert reply_target_for_task(_Task("cli")) is None
    assert reply_target_for_task(_Task(None)) is None
