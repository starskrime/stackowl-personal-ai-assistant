"""The note that says a turn was cut short must reach the user, not just be written.

WHY THIS EXISTS, and it is the same defect as its sibling one class of text over.

`test_a_budget_stop_is_never_silent.py` pins that a capped turn appends
"[stopped: ...]". It asserts that by reading the SOURCE of `execute.py` — the note
is verified where it is WRITTEN. Nothing verified it where it is DELIVERED, and a
step in between removes it.

MEASURED 2026-09-06 on the live database. Of the 74 budget-capped turns since
Bakir's 2026-08-24 report, 50 told the user plainly, 14 delivered an honest apology
floor, and **10 said something else** — a substantive answer with nothing to
indicate it had been cut off. Correlated against the logs, of the silent ones for
which log evidence still exists **every single one had been summarised** (4 of 4),
against **0 of 58** that kept their note. The oldest retained log is 2026-08-28, so
the four older silent turns are unknowable rather than counter-evidence.

The most recent reached Telegram on 2026-09-06 at 14:05:04:

    14:05:01  [budget] gate: cap reached — stopping
    14:05:01  [pipeline] execute: budget cap reached — stopping with partial
    14:05:04  [pipeline] deliver: length_terse summarized      <- the note dies here
    14:05:04  [notifications] router.deliver: delivered

`execute` appended the note; `deliver`'s terse summariser fed the whole reply to a
fast-tier model and delivered its rewrite. That is Bakir's original report — "he
asked a question, got back one line, and was told none of it" — recurring on a path
the repair did not cover.

WHY THE EXISTING PROTECTION MISSED IT. `_summarize_unless_floor` already refuses to
compress a FLOOR, added 2026-08-29 after a model paraphrased a guarantee into a
falsehood. A budget-capped turn is not a floor: it is a genuine partial answer with
an honesty suffix, so it took the summariser path. Same seam, same failure, one
class of text not covered — an actuator wired on only some paths.

The note is SPLIT OFF rather than compression being skipped. The owner asked for
terse output and the sentence is fixed machine text with nothing to compress, so
the user keeps both the preference and the truth.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.steps import deliver as deliver_mod

pytestmark = pytest.mark.asyncio


class _Services:
    provider_registry = object()
    preference_store = None


class _State:
    trace_id = "t-stop"


def _install_summariser(monkeypatch, returns: str) -> list[str]:
    """Make the fast-tier call return *returns*, and record what it was given."""
    seen: list[str] = []

    async def _fake_complete(provider, model, messages, **kw):  # noqa: ANN001
        seen.append(messages[-1].content)
        return type("R", (), {"result": type("C", (), {"content": returns})()})()

    from stackowl.interaction import classifier_base

    monkeypatch.setattr(classifier_base, "resolve_fixed_tier",
                        lambda *a, **k: ("provider", "model"))
    monkeypatch.setattr(classifier_base, "safe_complete", _fake_complete)
    return seen


class TestTheNoteSurvivesTheSummariser:
    @pytest.mark.tripwire
    async def test_a_stop_note_is_still_there_after_compression(self, monkeypatch) -> None:
        """THE DEFECT. The model returns prose WITHOUT the note — as the live one did."""
        seen = _install_summariser(monkeypatch, "A much shorter answer.")
        note = "[stopped: I ran out of steps for this turn before I could finish.]"
        text = ("Here is a long substantive answer. " * 40) + "\n\n" + note

        out = await deliver_mod._summarize_if_terse(text, _Services(), _State())

        assert note in out, (
            "the honesty note was compressed away — the user is told a cut-off turn "
            "is a complete answer, which is the report this note exists to answer"
        )
        assert "A much shorter answer." in out, "the body should still be compressed"

    async def test_the_note_never_reaches_the_model_at_all(self, monkeypatch) -> None:
        """Splitting, not trusting: a model asked to keep a sentence sometimes will
        not, which is how this failed in the first place."""
        seen = _install_summariser(monkeypatch, "short")
        note = "[stopped: I ran out of steps for this turn before I could finish.]"
        text = ("body text here. " * 60) + "\n\n" + note

        await deliver_mod._summarize_if_terse(text, _Services(), _State())

        assert seen, "the summariser was never called — the test proves nothing"
        assert "[stopped:" not in seen[0], (
            "the note was handed to the model, so keeping it is left to chance"
        )

    async def test_an_ordinary_reply_is_unchanged_by_this(self, monkeypatch) -> None:
        """The guard must be narrow. A reply with no note compresses exactly as before."""
        _install_summariser(monkeypatch, "compressed")
        text = "an ordinary answer with no note at all. " * 40

        out = await deliver_mod._summarize_if_terse(text, _Services(), _State())

        assert out == "compressed", "a note-free reply must be byte-identical to before"

    async def test_the_note_survives_a_summariser_that_fails(self, monkeypatch) -> None:
        """Every exit re-attaches it, not only the happy one — a failed summariser
        returning the body alone would drop the note just as effectively."""
        from stackowl.interaction import classifier_base

        async def _fail(provider, model, messages, **kw):  # noqa: ANN001
            return type("R", (), {"result": None})()

        monkeypatch.setattr(classifier_base, "resolve_fixed_tier",
                            lambda *a, **k: ("provider", "model"))
        monkeypatch.setattr(classifier_base, "safe_complete", _fail)
        note = "[stopped: I ran out of steps for this turn before I could finish.]"
        text = ("body. " * 80) + "\n\n" + note

        out = await deliver_mod._summarize_if_terse(text, _Services(), _State())

        assert note in out


class TestTheSplitterItself:
    async def test_it_only_matches_a_TRAILING_note(self) -> None:
        """A bracketed phrase mid-answer is the user's content, not our suffix."""
        body, note = deliver_mod._split_stop_note(
            "I looked at [stopped: something] in the middle and continued."
        )

        assert note == ""
        assert body.endswith("continued.")

    async def test_a_note_free_text_is_returned_unchanged(self) -> None:
        body, note = deliver_mod._split_stop_note("plain answer")

        assert (body, note) == ("plain answer", "")
