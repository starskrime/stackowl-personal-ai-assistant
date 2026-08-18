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

from stackowl.pipeline.progress.vocabulary import _EN, ProgressKey


class TestTheAckIsASingleSpell:
    def test_it_is_exactly_one_word(self) -> None:
        ack = _EN[ProgressKey.ACK]

        assert isinstance(ack, str)
        assert len(ack.split()) == 1, f"the ack is not a single word: {ack!r}"

    def test_it_carries_no_trailing_ellipsis(self) -> None:
        """The other states trail off because they describe ongoing work. A spell
        is spoken once — an ellipsis would make it read as an unfinished sentence
        rather than an incantation."""
        assert not _EN[ProgressKey.ACK].endswith("…")

    def test_it_is_the_summoning_charm(self) -> None:
        assert _EN[ProgressKey.ACK] == "Accio"

    def test_the_other_states_are_untouched(self) -> None:
        """Only the instant ack changes. The states that describe real work still
        say what the platform is actually doing — replacing those with spells would
        trade information the user needs for novelty they did not ask for."""
        assert _EN[ProgressKey.SEARCH_WEB] == "Searching the web…"
        assert _EN[ProgressKey.SYNTH] == "Writing your answer…"
