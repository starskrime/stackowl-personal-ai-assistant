"""Saying an owl's name is not an instruction to go and do something.

BAKIR, 2026-08-22: "I did ask something else and platform did total different."

MEASURED, from his Telegram transcript and the live database:

    09:57  Bakir      "Brain?"
    09:57  StackOwl   "— new conversation (the previous one went quiet) —"
    09:57  StackOwl   "✓ done in 266s"
    10:01  StackOwl   "I couldn't fully complete this: ?. The capability that
                       failed: owl_build. ... 'syshealth' was created by another
                       owl — you may only modify owls you created."
    10:06  StackOwl   "Created. job_id: 0 — one-time, fires Sunday Aug 23 ..."

He got someone's attention. The platform ran for 266 seconds, tried to edit an owl
it did not own, and scheduled a cron job about a drill list from an old
conversation.

THE CAUSE, in the database rather than inferred::

    messages.content : '?'
    tasks.goal       : ?

`_resolve_vocative` strips leading ``[\\s,:;]`` after the name — but not ``?``. So
"Brain?" resolved to ``("Brain", "?")``, and a single question mark became both the
stored user message and the durable task's goal. Handed a goal with no content, the
agent searched memory for something to do and acted on what it found.

The empty case was already handled (`goal[:4000] or "(empty turn)"`). A remainder
of punctuation is not empty, so it walked straight through as a real instruction.
"""

from __future__ import annotations

import pytest

from stackowl.gateway.scanner import _addressed_only, _has_content


class TestContentIsUnicodeNotAWordList:
    """This platform is multilingual; a keyword check answers in one language."""

    @pytest.mark.parametrize("text", ["?", "!", "...", "  ", "", "??!", "—", ":"])
    def test_punctuation_only_is_not_content(self, text: str) -> None:
        assert _has_content(text) is False

    @pytest.mark.parametrize(
        "text",
        ["hello", "что нового", "ne oldu", "状態は", "7", "b", "ok?"],
    )
    def test_any_script_counts_as_content(self, text: str) -> None:
        assert _has_content(text) is True


class TestTheLiveCase:
    def test_brain_question_mark_routes_the_WHOLE_message(self) -> None:
        """THE DEFECT. The remainder was "?"; the owl must see "Brain?"."""
        assert _addressed_only("Brain?", "?") == "Brain?"

    def test_a_real_instruction_is_still_stripped(self) -> None:
        """The strip must keep working — this is the common path.

        "Brain what is my schedule" must reach Brain as the question, not with its
        own name pinned to the front, or every routed turn gets noisier.
        """
        assert _addressed_only(
            "Brain what is my schedule", "what is my schedule"
        ) == "what is my schedule"

    def test_a_bare_name_with_no_punctuation_also_routes_whole(self) -> None:
        assert _addressed_only("Brain", "") == "Brain"

    def test_a_trailing_vocative_with_no_content_routes_whole(self) -> None:
        """"..., Brain?" — the terminal form of the same mistake."""
        assert _addressed_only("?, Brain?", "?") == "?, Brain?"

    def test_a_one_letter_instruction_is_still_an_instruction(self) -> None:
        """The guard must not swallow terse but real input.

        "Brain y" is content — deciding it is not would be this fix causing the
        opposite failure, silently dropping what the user actually typed.
        """
        assert _addressed_only("Brain y", "y") == "y"
