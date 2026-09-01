"""Formatting is a property of the CHANNEL, not of a per-user preference.

THE REPORT. Bakir, on Telegram 2026-08-31: "do not send me report in md format
if i did not specifically asked for it. Always format output for the channel
which users interacting". The platform answered "done in 9s — from now on here:
no asterisks, no raw tables and replies kept short", which is a PREFERENCE
written against one owner key. He rejected that: "you should fix the core off
issue why it is happening not what happaned".

THE CORE ISSUE, MEASURED THE SAME DAY. Two searches, both empty, one on each
side of the model:

* ``grep channel src/stackowl/owls/base_prompt.py`` -> NOTHING. The model
  composing a reply was never told where the reply was going. It wrote a
  document because nothing said it was writing into a chat window.
* ``deliver.py``'s enforcement opened with ``if not prefs: return state`` and
  its own docstring said "channel-agnostic". So output shape was ENTIRELY a
  per-user preference — a user who had never complained got no channel policy
  at all.

Those two facts together are why "always format for the channel" could only be
recorded as one person's taste: there was nowhere else to put it.

IT IS NOT A TASTE, and the terminal proves it. ``cli_adapter.send_text`` writes
the string straight to the app, and nothing under ``src/stackowl/tui/`` renders
markdown (measured, zero hits for Markdown/from_markup/rich.markdown). A
terminal displays ``**bold**`` as four literal asterisks whatever anyone
prefers.

THE COMPOSITION IS ONE-DIRECTIONAL. The channel floor is the base and the
preference narrows it. A preference may make output PLAINER; it may not ask a
channel to render something it cannot. Anything else re-opens the same hole
from the other end.

ONE RECORD, BOTH SIDES. ``ChannelShape.describe`` rides the per-turn context
into generation; ``ChannelShape.floor`` is enforced at delivery. Same object,
so what we ask the model for and what we enforce on its output cannot drift —
the "two copies of one rule" shape this codebase has paid for repeatedly.
"""

from __future__ import annotations

import json
from datetime import datetime

from stackowl.channels._format import (
    CHANNEL_SHAPES,
    OUTPUT_STYLE_KEY,
    OutputStyle,
    channel_shape,
    resolve_output_style,
)
from stackowl.owls.base_prompt import strip_turn_context, volatile_turn_context

_NOW = datetime(2026, 8, 31, 19, 56)


# --------------------------------------------------------------------------- #
# The delivery side: a channel floor applies with NO preference set            #
# --------------------------------------------------------------------------- #


def test_a_terminal_gets_plain_text_without_anyone_asking() -> None:
    """The exact hole: zero preferences used to mean zero channel policy."""
    style = resolve_output_style({}, channel="cli")
    assert style.markdown == "off", (
        "a terminal renders no markdown at all, so the floor must apply to a "
        "user who has never stated a preference — this is the defect Bakir "
        "reported as 'always format output for the channel'"
    )
    assert style.enforce("Here is the **summary** you asked for") == (
        "Here is the summary you asked for"
    )


def test_an_unknown_channel_is_left_alone() -> None:
    """Silence, not a guess. Inventing a shape for an unrecognised destination
    is how a working render gets quietly degraded."""
    assert channel_shape("something-new") is None
    assert resolve_output_style({}, channel="something-new") == OutputStyle()
    assert resolve_output_style({}, channel=None) == OutputStyle()


def test_a_chat_channel_keeps_its_working_render() -> None:
    """The Telegram/Slack adapters DO convert GFM; the defect there was writing
    a document, not the escaping. A floor that stripped markdown would trade a
    working render for an unmeasured one."""
    assert resolve_output_style({}, channel="telegram") == OutputStyle()
    assert resolve_output_style({}, channel="slack") == OutputStyle()


# --------------------------------------------------------------------------- #
# The composition: a preference may narrow, never widen                        #
# --------------------------------------------------------------------------- #


def _prefs(**style: str) -> dict[str, str]:
    return {OUTPUT_STYLE_KEY: json.dumps(style)}


def test_a_preference_cannot_ask_a_terminal_for_bold() -> None:
    """The one-directional invariant, stated as the case that would break it."""
    style = resolve_output_style(_prefs(markdown="full"), channel="cli")
    assert style.markdown == "off", (
        "a preference widened the channel floor — a terminal cannot render "
        "bold however anyone prefers it, and allowing this re-opens the hole "
        "from the other end"
    )


def test_a_preference_still_makes_output_plainer() -> None:
    """The direction that must keep working: the user asked for less."""
    style = resolve_output_style(_prefs(emoji="off", length="terse"), channel="telegram")
    assert style.emoji == "off"
    assert style.length == "terse"


def test_a_preference_on_another_field_does_not_lose_the_floor() -> None:
    """A user who set ONE field must still get the channel's policy on the rest
    — the composition is per-field, not whole-record."""
    style = resolve_output_style(_prefs(emoji="off"), channel="cli")
    assert style.markdown == "off" and style.emoji == "off"


def test_narrow_keeps_the_plainer_value_of_every_field() -> None:
    plain = OutputStyle(markdown="off", tables="off", emoji="off", length="terse")
    rich = OutputStyle()
    assert plain.narrow(rich) == plain
    assert rich.narrow(plain) == plain, "narrow must be symmetric in effect"


def test_narrow_mutates_neither_input() -> None:
    floor, pref = OutputStyle(markdown="off"), OutputStyle(emoji="off")
    floor.narrow(pref)
    assert floor.markdown == "off" and floor.emoji == "on"
    assert pref.markdown == "full" and pref.emoji == "off"


def test_links_is_not_treated_as_a_richness_axis() -> None:
    """A titled link is not "more" than a bare URL, so it composes by intent:
    the floor wins only when it states something, else the preference does."""
    assert OutputStyle().narrow(OutputStyle(links="titles")).links == "titles"
    assert OutputStyle(links="titles").narrow(OutputStyle()).links == "titles"


def test_a_corrupt_stored_style_still_gets_the_channel_floor() -> None:
    """Degrading to defaults must not degrade past the channel — a broken
    preference row is not a licence to render markdown into a terminal."""
    style = resolve_output_style({OUTPUT_STYLE_KEY: "{not json"}, channel="cli")
    assert style.markdown == "off"


# --------------------------------------------------------------------------- #
# The generation side: the model is TOLD where it is writing                   #
# --------------------------------------------------------------------------- #


def test_the_model_is_told_its_destination() -> None:
    """Before this, base_prompt mentioned no channel anywhere."""
    context = volatile_turn_context(_NOW, channel="telegram")
    assert CHANNEL_SHAPES["telegram"].describe in context


def test_the_terminal_gets_its_own_instruction() -> None:
    context = volatile_turn_context(_NOW, channel="cli")
    assert CHANNEL_SHAPES["cli"].describe in context
    assert CHANNEL_SHAPES["telegram"].describe not in context


def test_no_channel_says_nothing() -> None:
    """A scheduled job with no destination must not be told it is in a chat."""
    context = volatile_turn_context(_NOW)
    assert all(s.describe not in context for s in CHANNEL_SHAPES.values())
    assert "Right now it is" in context


def test_the_clock_survives_the_addition() -> None:
    assert "Right now it is" in volatile_turn_context(_NOW, channel="telegram")


def test_the_destination_sentence_is_stripped_from_user_facing_text() -> None:
    """The 2026-08-15 leak, which this must not re-open: the context rides the
    user's message, so a model can copy it into a tool argument and the user is
    shown our own scaffolding. Every declared description must strip."""
    for name in CHANNEL_SHAPES:
        composed = volatile_turn_context(_NOW, channel=name) + "\n\nwhat agents i have"
        assert strip_turn_context(composed) == "what agents i have", (
            f"the {name} destination sentence survived stripping and would "
            f"reach a user or a tool argument"
        )


# --------------------------------------------------------------------------- #
# One record, both sides                                                       #
# --------------------------------------------------------------------------- #


def test_every_declared_channel_says_something_and_is_enforceable() -> None:
    """A shape with an empty description instructs nothing; a shape whose floor
    is unreachable enforces nothing. Either half alone is decoration."""
    for name, shape in CHANNEL_SHAPES.items():
        assert shape.describe.strip(), f"{name} declares no shape to the model"
        assert resolve_output_style({}, channel=name) == shape.floor, (
            f"{name}'s declared floor is not what the delivery seam resolves — "
            f"the instruction and the enforcement have drifted apart"
        )


def test_the_shape_is_read_case_insensitively() -> None:
    """``state.channel`` is not guaranteed to be casefolded upstream."""
    assert channel_shape("Telegram") is CHANNEL_SHAPES["telegram"]
    assert channel_shape("  CLI  ") is CHANNEL_SHAPES["cli"]
