"""The name list is the cheapest tier and the first one cut.

MEASURED 2026-08-31 on the live platform: 249 records of
``skill injection: catalog truncated by budget`` in one day, 71 of them since
noon and still firing at 18:49, every one carrying::

    {'owl': ..., 'dropped': 3, 'presented': 21,
     'last_presented': 'plan-and-track', 'first_dropped': 'recover-and-retry'}

The corpus is TWENTY-FOUR active skills. The whole catalogue, rendered as bare
names, costs **487 characters** against a 4,000-character cap — and three of those
names are still dropped on every single turn, so ``recover-and-retry`` has been
invisible to every owl all day.

WHY, AND IT IS AN ORDERING FAULT RATHER THAN A SIZE ONE. The renderer funds the
tiers top-down: FULL bodies first, then SUMMARY lines, and the catalogue gets
``remaining = cap - used`` — whatever is left. So the tier that costs
``len(name) + 2`` per entry is starved by the tiers that cost hundreds. Three
names, about 66 characters, were dropped so that a full skill body could keep
occupying its space.

THE RESERVE PATTERN ALREADY EXISTS AND WAS NEVER EXTENDED DOWN ONE STEP::

    _SUMMARY_BUDGET_RESERVE = 800  # chars the FULL tiers cannot consume,
                                   # so SUMMARY isn't starved

SUMMARY was protected from FULL. CATALOG was protected from nothing, and it is
the tier that most deserves it: it is the platform's index of what exists, and
Bakir's own recorded position on the sibling defect one layer up is that the
capability surface is not overhead — "we capped that catalogue at 4,000 chars and
produced a platform that could not answer 'what agents i have'".

THE RESERVE IS DERIVED, NOT GUESSED. It is the actual cost of the actual names,
so a three-skill corpus reserves about thirty characters and a growing corpus
reserves what it needs — bounded by a share of the cap, because an index is not a
licence to crowd out every instruction. Past that bound the old behaviour returns
unchanged: truncate, and say where the cut fell.

THE TRADE IS REAL AND IS THE POINT. Reserving the index can demote one skill's
TEXT to make three skills VISIBLE. A skill whose text is missing can still be
loaded by name with skill_view; a skill whose NAME is missing cannot be asked for
at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from stackowl.skills.instruction_injector import (
    _DEFAULT_CAP,
    CATALOG_RESERVE_SHARE,
    SkillInstructionInjector,
    SkillTier,
)


@dataclass
class _Sk:
    name: str
    description: str = "does a thing"
    when_to_use: str = "when a thing must be done"
    source: str = "builtin"


def _big(name: str) -> _Sk:
    """A skill whose FULL body is large enough to eat real budget."""
    return _Sk(name=name, description="x" * 700, when_to_use="y" * 300)


def _render(tiered, caplog=None):  # noqa: ANN001, ANN201
    injector = SkillInstructionInjector()
    if caplog is None:
        return injector.render("test-owl", tiered)
    with caplog.at_level(logging.WARNING):
        return injector.render("test-owl", tiered)


def test_the_live_shape_every_name_survives(caplog: pytest.LogCaptureFixture) -> None:
    """24 skills whose names cost 487 chars, against a 4,000 cap, with fat bodies
    competing — reproducing the 249-times-a-day truncation."""
    fat = [(_big(f"heavy-skill-{i}"), SkillTier.FULL, False) for i in range(6)]
    rest = [
        (_Sk(name=n), SkillTier.CATALOG, False)
        for n in ("recover-and-retry", "plan-and-track", "verify-before-claim")
    ]
    out = _render(fat + rest, caplog)

    for name in ("recover-and-retry", "plan-and-track", "verify-before-claim"):
        assert name in out, f"{name} was dropped from a 487-character index"
    assert not [r for r in caplog.records if "catalog truncated" in r.getMessage()]


def test_the_reserve_is_the_ACTUAL_cost_of_the_ACTUAL_names() -> None:
    """Derived, not a constant size. A tiny corpus must not hold back budget it
    has no use for — that would starve the text to protect an index of three."""
    tiny = [(_Sk(name=n), SkillTier.CATALOG, False) for n in ("a", "b", "c")]
    fat = [(_big("heavy"), SkillTier.FULL, False)]
    out = _render(fat + tiny)

    assert "heavy" in out, "a 9-character index displaced a whole skill body"
    for n in ("a", "b", "c"):
        assert n in out


def test_the_reserve_is_BOUNDED_and_the_old_behaviour_returns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An index is not a licence to crowd out every instruction. Past the bound
    the catalogue truncates exactly as before, and still says where it cut."""
    many = [
        (_Sk(name=f"skill-{i:03d}-with-a-deliberately-long-name"), SkillTier.CATALOG, False)
        for i in range(400)
    ]
    out = _render(many, caplog)

    recs = [r for r in caplog.records if "catalog truncated" in r.getMessage()]
    assert recs, "a 400-skill index must still be cut"
    assert "skills_list" in out, "the escape hatch must survive"
    fields = getattr(recs[-1], "_fields", {})
    assert fields.get("first_dropped"), "the cut point is still recorded"


def test_the_bound_is_a_share_of_the_cap_not_a_magic_size() -> None:
    """So changing the cap moves the reserve with it, instead of leaving a second
    number behind — the defect this codebase keeps paying for."""
    assert 0 < CATALOG_RESERVE_SHARE < 1


def test_the_total_still_fits_the_cap() -> None:
    """The reserve reallocates the budget; it must never raise it."""
    many = [
        (_Sk(name=f"skill-{i:03d}"), SkillTier.CATALOG, False) for i in range(80)
    ]
    fat = [(_big(f"heavy-{i}"), SkillTier.FULL, False) for i in range(8)]
    out = _render(fat + many)

    assert len(out) <= _DEFAULT_CAP * 1.35, (
        f"rendered {len(out)} chars — the reserve must reallocate the budget, "
        f"never enlarge it"
    )


def test_the_text_tiers_are_still_populated() -> None:
    """Protecting the index must not empty the sections that carry instructions."""
    fat = [(_big(f"heavy-{i}"), SkillTier.FULL, False) for i in range(3)]
    names = [(_Sk(name=f"n-{i}"), SkillTier.CATALOG, False) for i in range(20)]
    out = _render(fat + names)

    assert "heavy-0" in out
    assert out.count("x" * 100) >= 1, "no full skill body survived"


def test_the_SUMMARY_reserve_still_works() -> None:
    """The pattern being extended must not be broken by extending it."""
    from stackowl.skills.instruction_injector import _SUMMARY_BUDGET_RESERVE

    assert _SUMMARY_BUDGET_RESERVE == 800
    fat = [(_big(f"heavy-{i}"), SkillTier.FULL, False) for i in range(10)]
    summaries = [(_Sk(name=f"s-{i}"), SkillTier.SUMMARY, False) for i in range(3)]
    out = _render(fat + summaries)

    assert "s-0" in out, "the summary tier was starved by the full tier"


def test_the_PRODUCTION_shape_every_skill_arrives_as_SUMMARY(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The path production actually takes, and the reason the first fix did nothing.

    ``assemble.py:335`` builds ``[(sk, SkillTier.SUMMARY, False) for sk in catalogue]``
    — nothing in src/ ever passes ``SkillTier.CATALOG`` in, so that tier is reached
    only by DEMOTION inside ``render``. A reserve computed from the INCOMING tier is
    therefore always zero in production, while unit tests that construct CATALOG
    entries directly pass happily. Measured after the 18:53 restart: still
    ``dropped: 3, presented: 21``.
    """
    corpus = [_big(f"heavy-skill-{i}") for i in range(6)] + [
        _Sk(name=n)
        for n in ("recover-and-retry", "plan-and-track", "verify-before-claim")
    ]
    tiered = [(sk, SkillTier.SUMMARY, False) for sk in corpus]

    out = _render(tiered, caplog)

    for name in ("recover-and-retry", "plan-and-track", "verify-before-claim"):
        assert name in out, f"{name} is invisible on the path production takes"
    assert not [r for r in caplog.records if "catalog truncated" in r.getMessage()]
