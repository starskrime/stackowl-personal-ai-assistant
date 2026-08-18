"""The instant acknowledgement is one word, and it is a spell.

BAKIR, 2026-08-18: "I want instant back words be single word and something from
harry potter magic words."

WHY "Accio" AND NOT ANOTHER. It is the Summoning Charm, so the message means "your
question landed and I am summoning the answer" — which is precisely what
ProgressKey.ACK is for ("turn received, nothing started yet"). A spell chosen for
sound alone would be decoration; this one says what the state means.

A SIDE EFFECT WORTH KEEPING. Every other template in the vocabulary is English
prose that needs a translation per language bundle. A spell is pseudo-Latin and
reads identically everywhere, so the ONE line the user sees first no longer depends
on a bundle existing for their language.
"""

from __future__ import annotations

from stackowl.pipeline.progress.vocabulary import (
    _ACK_SPELLS,
    ProgressKey,
    ack_spell,
    render,
)


class TestEverySpellMeetsTheRequest:
    def test_every_one_is_a_single_word(self) -> None:
        """The request holds no matter which spell is drawn — so it is asserted
        over the WHOLE rotation, not over whichever one happens to come up."""
        bad = [s for s in _ACK_SPELLS if len(s.split()) != 1]

        assert not bad, f"not single words: {bad}"

    def test_none_carries_a_trailing_ellipsis(self) -> None:
        """The other states trail off because they describe ongoing work. A spell
        is spoken once — an ellipsis would make it read as an unfinished sentence
        rather than an incantation."""
        assert not [s for s in _ACK_SPELLS if s.endswith("…")]

    def test_there_are_actually_several(self) -> None:
        """Bakir: "multiple different words instead of single hardcoded"."""
        assert len(set(_ACK_SPELLS)) >= 3


class TestOneSpellPerTURN:
    def test_the_same_turn_always_gets_the_same_spell(self) -> None:
        """The property that keeps this from looking broken. The live status is
        EDITED every few seconds and each edit re-renders; an unseeded draw would
        change the word mid-wait."""
        assert len({ack_spell("turn-abc") for _ in range(20)}) == 1

    def test_different_turns_get_different_spells(self) -> None:
        """Otherwise the rotation exists and is never seen."""
        seen = {ack_spell(f"turn-{i}") for i in range(40)}

        assert len(seen) > 1, f"the rotation never rotates: {seen}"

    def test_it_renders_with_the_glyph(self) -> None:
        out = render(ProgressKey.ACK, seed="t")

        assert out.startswith("⏳ ")
        assert out.split(" ", 1)[1] in _ACK_SPELLS

    def test_an_unusable_seed_still_yields_a_spell(self) -> None:
        """A status line must never break a turn."""
        class _Boom:
            def __str__(self) -> str:
                raise RuntimeError("no")

        assert ack_spell(_Boom()) in _ACK_SPELLS


class TestOnlyTheAckChanged:
    def test_the_other_states_are_untouched(self) -> None:
        """Only the instant ack changes. The states that describe real work still
        say what the platform is actually doing — replacing those with spells would
        trade information the user needs for novelty they did not ask for."""
        from stackowl.pipeline.progress.vocabulary import _EN

        assert _EN[ProgressKey.SEARCH_WEB] == "Searching the web…"
        assert _EN[ProgressKey.SYNTH] == "Writing your answer…"
