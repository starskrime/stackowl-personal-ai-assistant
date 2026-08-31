"""ESC-63 — trim `when_to_use` in the CATALOGUE so proven skills stay visible.

MEASURED 2026-08-30, four options against the live 20-skill corpus at the real
cap of 4,000. "used-visible" counts how many of the 8 skills that have ever
actually executed appear in the rendered block:

    A  today                            11 visible   used-visible 4/8   4,157 chars
    B  drop the 4 never-used dupes      11 visible   used-visible 4/8   4,157 chars
    C  trim when_to_use to 80 chars     17 visible   used-visible 8/8   4,124 chars
    D  drop when_to_use entirely        20 visible   used-visible 8/8   3,422 chars

B IS THE RESULT THAT MATTERS. Removing near-duplicate skills — the intuitive fix,
and the one I recommended before measuring — changes NOTHING. C restores every
proven skill.

Bakir's stated risk on this escalation was that "cutting the when signal could
lower loads while raising visibility". C keeps the signal in shortened form and
reaches the same 8/8 as dropping it entirely, so the trade he was worried about
is not one that has to be made.

WHAT WAS BEING LOST: `verify-before-claim` (executed 3x) and `write-your-own-skill`
were truncated OUT of the prompt while five never-executed `incident_*` templates
sat in the budget.
"""

from __future__ import annotations

from stackowl.skills.instruction_injector import (
    WHEN_TO_USE_CATALOGUE_CHARS,
    SkillInstructionInjector,
    SkillTier,
    _resolve_text,
)


class _Sk:
    def __init__(self, name: str, description: str = "d", when_to_use: str = "") -> None:
        self.name = name
        self.source = "builtin"
        self.description = description
        self.when_to_use = when_to_use


def test_a_long_when_to_use_is_trimmed() -> None:
    long = "x" * 400
    text = _resolve_text(_Sk("s", "desc", long))
    assert len(text) < 400
    assert "x" * WHEN_TO_USE_CATALOGUE_CHARS in text


def test_a_SHORT_when_to_use_is_untouched() -> None:
    """Trimming must not rewrite what already fits — most skills are under the
    limit and their text should reach the model verbatim."""
    text = _resolve_text(_Sk("s", "desc", "use when deploying"))
    assert text == "desc — use when deploying"


def test_the_description_is_NOT_trimmed() -> None:
    """Only `when_to_use` is capped here. D10.2 already caps description at 60,
    and capping it twice in two places is the two-copies-of-one-rule shape."""
    text = _resolve_text(_Sk("s", "D" * 200, "short"))
    assert "D" * 200 in text


def test_an_ABSENT_when_to_use_still_renders() -> None:
    assert _resolve_text(_Sk("s", "desc", "")).startswith("desc")


def test_trimming_restores_the_PROVEN_skills_to_the_catalogue() -> None:
    """The whole point, as a property rather than a fixture: with 20 skills whose
    when_to_use is long, trimming must fit materially more of them in the budget."""
    inj = SkillInstructionInjector()
    skills = [_Sk(f"skill-{i:02d}", "a description", "W" * 300) for i in range(20)]
    out = inj.render("secretary", [(s, SkillTier.SUMMARY, False) for s in skills], cap=4000)
    visible = sum(1 for s in skills if s.name in out)
    assert visible >= 15, f"only {visible} of 20 fit after trimming"
