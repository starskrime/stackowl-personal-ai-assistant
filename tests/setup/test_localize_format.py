"""Tests for localize_format slot interpolation + self-heal floor catalog keys."""

from stackowl.setup.localize import localize_format


def test_localize_format_fills_slots() -> None:
    # The five-slot ``self_heal_floor`` template this used to target was DELETED
    # on 2026-09-03: the floor is composed from per-sentence keys, because a
    # whole-message template renders its unfilled slots as bare punctuation.
    out = localize_format(
        "self_heal_floor_s_capability", "en", failed_capability="browser_browse",
    )
    assert "browser_browse" in out
    assert out  # non-empty


def test_localize_format_missing_slot_does_not_raise() -> None:
    """Resilient: a missing slot leaves a readable string, never a KeyError.

    Asserts the BLANK, not merely non-emptiness. The previous version passed
    ``goal="x"`` to the five-slot template and asserted only ``out`` — which was
    true precisely BECAUSE the four unfilled slots rendered as punctuation. It was
    one of the tests that made the empty-slot defect invisible.
    """
    out = localize_format("self_heal_floor_s_capability", "en")
    assert out
    assert "The capability that failed: ." in out, (
        "an unfilled slot should still render safely here — this is the raw "
        "primitive; it is synthesize_floor's job never to CALL it without data"
    )


def test_localize_format_unknown_key_returns_nonempty() -> None:
    # Unknown key falls back (to the key itself per localize) — still non-empty, no crash.
    out = localize_format("totally_unknown_key_xyz", "en", goal="x")
    assert out


def test_self_heal_floor_minimal_key_exists() -> None:
    from stackowl.setup.localize import localize

    assert localize("self_heal_floor_minimal", "en")  # static non-empty fallback for the floor


def test_floor_template_localized_second_language() -> None:
    # multilingual: a second language entry exists and differs from en (proves not English-only)
    from stackowl.setup.localize import localize

    assert localize("self_heal_floor_s_goal", "de") != localize("self_heal_floor_s_goal", "en")
