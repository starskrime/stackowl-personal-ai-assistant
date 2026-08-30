"""A Telegram chat id that arrives as a string is still a Telegram chat id.

MEASURED 2026-08-30 while auditing D01.7, whose acceptance recorded:

    delivery: "... ZERO 'falling back to _last_chat_id' warnings — which was the
    whole risk."

That zero is now **29**, every one carrying ``target: '72055773'`` — the operator's
own chat id, correct, merely arriving as a ``str`` where an ``int`` was expected.
First seen 2026-08-26T23:26:16, latest 2026-08-30T01:10:22, so it long predates
this session.

THE CODE'S REASONING IS EXPLICIT AND FALSIFIED BY THAT DATA::

    # Telegram only ever delivers int chat_id targets; a str (Slack
    # channel/thread_ts) cannot reach the Telegram adapter by construction
    # (each turn is delivered by its OWN channel adapter).
    # Log loudly if one ever does, then fall back to _last_chat_id.

The premise is that a ``str`` means a FOREIGN id. Measured, it does not: it is this
adapter's own chat id, stringified upstream. So the guard discards a CORRECT
destination and re-derives one from ``_last_chat_id``.

WHY NOTHING BROKE, AND WHY THAT IS NOT REASSURING. With a single chat,
``_last_chat_id`` is the same chat, so all 29 landed correctly by luck of the
population. With two active chats the fallback delivers one conversation's answer
to the other — and "the destination was guessed" is precisely the class this tree
has already paid for twice today (a bare channel name that could never complete,
and a turn task with no address).

SAME INT/STR TRAP, OTHER SIDE. ``destination_for_turn`` carries a scar from it —
"its first live run failing with 'int' object has no attribute 'strip'" — and is
parametrized over ``[72055773, "72055773", "C123ABC", "+15551234"]``. This is the
send side of the same coin.

THE WARNING IS KEPT for a genuinely foreign id, which is what it was written for.
"""

from __future__ import annotations

import pytest

from stackowl.channels.telegram.adapter import coerce_chat_id


@pytest.mark.parametrize("raw", ["72055773", " 72055773 ", 72055773])
def test_a_numeric_chat_id_is_USED_however_it_arrives(raw: object) -> None:
    """The defect: a stringified chat id was thrown away 29 times."""
    assert coerce_chat_id(raw) == 72055773


def test_a_GROUP_chat_id_survives() -> None:
    """Telegram group and supergroup ids are NEGATIVE.

    A coercion that only accepted digits would silently break every group chat —
    a much larger blast radius than the bug being fixed.
    """
    assert coerce_chat_id("-1001234567890") == -1001234567890


@pytest.mark.parametrize("raw", ["C123ABC", "+15551234", "general", "", None])
def test_a_genuinely_FOREIGN_id_is_still_refused(raw: object) -> None:
    """The guard must stay narrow.

    A Slack channel or a phone number really cannot address Telegram, and the loud
    warning it triggers is the behaviour this function must preserve.
    """
    assert coerce_chat_id(raw) is None


def test_a_float_or_bool_is_refused() -> None:
    """`bool` is an `int` subclass in Python, and a JSON `true` coerced to chat 1
    would deliver to whatever chat happens to be numbered 1 — a trap this repo has
    already written down once, in dev_ingress."""
    assert coerce_chat_id(True) is None
    assert coerce_chat_id(1.5) is None
