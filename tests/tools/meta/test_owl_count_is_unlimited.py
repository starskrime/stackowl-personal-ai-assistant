"""There is no cap on how many owls Bakir may have.

BAKIR, 2026-08-18: "Why does the platform have a limitation to create 5 owls only?
Remove that limitation and that should be unlimited."

He was at exactly 5 of 5, so it was blocking him.

MEASURED BEFORE REMOVING IT, because a cap usually protects something. The only
real cost of another owl is the ground-truth roster in the system prompt — name and
one-line role for every owl, on every call. On the live registry that is 69 chars
per owl (longest line 304), so fifty owls would cost ~862 tokens: 0.33% of a
262,144 window. The cap was not protecting the waist; five was simply a number.

WHAT STILL PROTECTS AGAINST A MESS, and is deliberately untouched: owl_build still
refuses a near-duplicate (existing_near_match), still enforces name quality, and
still requires consent for the tools a new owl is granted. Those catch the real
failure — twenty accidental variants of one persona — which a hard count never did:
a count blocks the sixth GOOD owl exactly as readily as the sixth junk one.

CONFIGURABLE, NOT DELETED. The limit becomes a setting defaulting to unlimited, so
an operator running a shared deployment can still bound it without a code change.
"""

from __future__ import annotations

from stackowl.tools.meta.owl_build_guards import over_owl_cap


class TestUnlimitedByDefault:
    def test_a_sixth_owl_is_allowed(self) -> None:
        """The exact case Bakir hit."""
        assert over_owl_cap(current=5, cap=0) is False

    def test_a_fiftieth_owl_is_allowed(self) -> None:
        assert over_owl_cap(current=49, cap=0) is False

    def test_the_default_setting_is_unlimited(self) -> None:
        from stackowl.config.settings import Settings

        assert Settings().owl_limits.max_agent_owls == 0


class TestAnOperatorCanStillBoundIt:
    def test_a_configured_cap_is_enforced(self) -> None:
        """A shared deployment may still want a limit — the point was that FIVE was
        arbitrary, not that a bound is never wanted."""
        assert over_owl_cap(current=5, cap=5) is True

    def test_under_a_configured_cap_is_allowed(self) -> None:
        assert over_owl_cap(current=4, cap=5) is False

    def test_a_negative_cap_is_treated_as_unlimited(self) -> None:
        """Garbage config must not lock the user out of their own platform."""
        assert over_owl_cap(current=99, cap=-1) is False
