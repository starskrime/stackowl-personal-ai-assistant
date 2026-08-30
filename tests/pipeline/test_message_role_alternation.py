"""D01.5 — the messages array must strictly alternate user/assistant.

THE MAP FILED THIS AS A "latent provider-compat bug class". It is not latent.
Running the production parser over the production rows on 2026-08-26: TWO OF FOUR
live conversations violate alternation, and one is the operator's own lane, whose
history opens

    A A A A U A U A U A U A U A

— four consecutive assistant turns. What is latent is the REJECTION, not the
violation: the only live provider is OpenAI-protocol and tolerates it, while a
strict provider rejects such an array outright. So this has been shipping every
turn and would break on the first strict backend added.

HOW IT HAPPENS, and neither half is a coding mistake on its own:

  A stored row is ``"User: X\\n\\nAssistant: Y"``. A SCHEDULED JOB writes one with
  an EMPTY user half — a daily digest nobody asked for in words.
  ``_parse_turns_to_messages`` skips empty halves, which is correct because a
  blank-content turn is itself rejected by providers. So that row contributes a
  bare assistant message, and several in a row produce the run above.

  ``_dedup_assistant_history`` produces the mirror image. Removing a repeated
  assistant turn from between two user turns leaves ``U U`` — measured on
  ``goal-owl_lifecycle-jobmarket``.

MERGE, NEVER SYNTHESISE. Joining a run keeps every word that was actually
produced and invents nothing. Slipping a placeholder user turn between two
assistant turns would put words in the user's mouth, and a fabricated turn in the
history is a worse defect than the one being repaired.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.message_shaping import merge_consecutive_roles
from stackowl.pipeline.steps.classify import (
    _dedup_assistant_history,
    _parse_turns_to_messages,
)
from stackowl.providers.base import Message


def _roles(msgs: list[Message]) -> str:
    return "".join("U" if m.role == "user" else "A" for m in msgs)


def test_a_run_of_assistant_turns_becomes_one() -> None:
    """The operator's own lane. Four scheduled digests, no user turn between."""
    msgs = [
        Message(role="assistant", content="digest 1"),
        Message(role="assistant", content="digest 2"),
        Message(role="assistant", content="digest 3"),
        Message(role="user", content="thanks"),
    ]
    out = merge_consecutive_roles(msgs)

    assert _roles(out) == "AU"
    assert "digest 1" in out[0].content
    assert "digest 3" in out[0].content, "merging must not drop content"


def test_two_user_turns_become_one() -> None:
    """The mirror image the dedup pass creates."""
    out = merge_consecutive_roles([
        Message(role="user", content="first ask"),
        Message(role="user", content="second ask"),
        Message(role="assistant", content="reply"),
    ])

    assert _roles(out) == "UA"
    assert "first ask" in out[0].content and "second ask" in out[0].content


def test_an_already_alternating_array_is_untouched() -> None:
    """The common case must be byte-identical — this runs on every turn."""
    msgs = [
        Message(role="user", content="a"),
        Message(role="assistant", content="b"),
        Message(role="user", content="c"),
    ]
    out = merge_consecutive_roles(msgs)

    assert [(m.role, m.content) for m in out] == [(m.role, m.content) for m in msgs]


def test_empty_and_single_are_safe() -> None:
    assert merge_consecutive_roles([]) == []
    one = [Message(role="user", content="x")]
    assert merge_consecutive_roles(one)[0].content == "x"


def test_THE_LIVE_ROW_SHAPE_that_caused_this() -> None:
    """Built from the real stored format, not a hand-invented one.

    A scheduled job writes ``"User: \\n\\nAssistant: <digest>"`` — the user half
    empty because nothing was typed. Two of those in a row are what produced the
    measured ``A A`` run. Generated through the SAME parser production uses, so
    the fixture cannot drift from the format it stands for.
    """
    rows = [
        "User: what is on today\n\nAssistant: three meetings",
        "User: \n\nAssistant: daily digest — 20 messages",
        "User: \n\nAssistant: daily digest — 4 messages",
    ]
    raw = _parse_turns_to_messages(rows)
    assert _roles(raw) == "UAAA", "the parser really does emit the bare assistant run"

    repaired = merge_consecutive_roles(_dedup_assistant_history(raw))
    assert _roles(repaired) == "UA", f"still violating after repair: {_roles(repaired)}"


def test_the_final_send_array_alternates_even_when_history_ends_with_user() -> None:
    """execute.py builds ``[*history, user]``. A turn whose reply was never stored
    leaves history ending on a user turn, and the append then makes two in a row —
    which is why the repair runs on the FINAL array and not only on history."""
    history = [
        Message(role="user", content="earlier ask"),
        Message(role="assistant", content="earlier reply"),
        Message(role="user", content="an ask whose reply was never stored"),
    ]
    final = merge_consecutive_roles([*history, Message(role="user", content="new ask")])

    assert _roles(final) == "UAU"
    assert "new ask" in final[-1].content
    assert "never stored" in final[-1].content, "the earlier ask must survive the merge"


@pytest.mark.parametrize(
    "roles",
    ["AAAA", "UUUU", "AUAUAU", "UAAUUA", "A", "U", "AAUU"],
)
def test_the_invariant_holds_for_any_sequence(roles: str) -> None:
    """The property itself, rather than the examples that motivated it."""
    msgs = [
        Message(role="user" if r == "U" else "assistant", content=f"m{i}")
        for i, r in enumerate(roles)
    ]
    out = merge_consecutive_roles(msgs)

    got = _roles(out)
    assert all(got[i] != got[i - 1] for i in range(1, len(got))), got
    # Nothing is lost: every original body still appears somewhere.
    joined = " ".join(m.content for m in out)
    for i in range(len(roles)):
        assert f"m{i}" in joined
