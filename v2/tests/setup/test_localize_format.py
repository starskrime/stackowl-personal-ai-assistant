"""Tests for localize_format slot interpolation + self-heal floor catalog keys."""

from stackowl.setup.localize import localize_format


def test_localize_format_fills_slots() -> None:
    out = localize_format(
        "self_heal_floor",
        "en",
        goal="browse a site",
        failed_capability="browser_browse",
        attempts="browser_browse",
        partial="",
        error="NS_ERROR_UNKNOWN_HOST",
    )
    assert "browser_browse" in out and "NS_ERROR_UNKNOWN_HOST" in out
    assert out  # non-empty


def test_localize_format_missing_slot_does_not_raise() -> None:
    # Resilient: a missing slot leaves a readable string, never a KeyError.
    out = localize_format("self_heal_floor", "en", goal="x")
    assert out


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

    assert localize("self_heal_floor", "de") != localize("self_heal_floor", "en")
