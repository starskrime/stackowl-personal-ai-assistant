"""A shipped skill was validated by a NAME LIST, so the one nobody listed was not.

MEASURED 2026-09-09, chasing a recorded product defect ("one skill that can never load,
229/229 boots"). That claim turned out to be `trending-research-owl`, which sits outside
every source dir and is ESC-127's open placement question — not this. Beside it in the
same loader, a second line: `[skills] loader.load_all: skill invalid — skipping`, twice,
both on 2026-09-06, both for a **shipped builtin**:

    /home/boss/.stackowl/skills/builtin/web-automation
    SkillMarkdownError: SKILL.md frontmatter is invalid YAML:
      mapping values are not allowed here
        description: Drive a website: navigate, read, fill forms, ex ...
                                    ^

An unquoted colon in a YAML scalar. The skill was simply ABSENT from the catalogue, and
the only signal was a WARNING in a log nobody reads. It is fixed (`56947626`, em-dash),
and the commit message says what caught it: *"the restart caught a defect I had just
introduced"* — a restart, not a test.

AND A TEST WOULD HAVE CAUGHT IT. `test_native_tier2_skills_journey.py` names
`web-automation`, calls `parse_skill_md` on it, and has existed since 2026-06-21. It did
not run on the path that shipped the break.

SO THE GAP IS NOT "NOTHING VALIDATES SHIPPED SKILLS" — 13 of the 14 are validated. It is
that the coverage is THREE HAND-MAINTAINED NAME LISTS where a declared property belongs:
*every skill this platform ships must load*. MEASURED: `chunked-pdf-summary` is named by
no tier journey, so nothing parses it at all, and the next skill added is unvalidated by
default in exactly the same way. That is CLAUDE.md's own shape — a name list standing in
for a property — and the property is one line of enumeration.

WHY THIS IS A PLATFORM DEFECT AND NOT A SETUP ONE. These files ship from
`src/stackowl/skills/_builtin/` and are seeded into every install, so a broken one is
broken for everyone who clones, not just this box. The user-visible symptom is a skill
that silently does not exist.

WHAT THIS DELIBERATELY DOES NOT DO. The tier journeys assert much more — body content,
tier placement, the wording of each skill's guidance — and they stay. This asserts only
LOADABILITY, which is the property that actually failed, over the population that
actually ships. Marked `tripwire` because a cross-cutting guard looks related to nothing
and would never be picked by a targeted path — which is precisely how the break shipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.skills.manifest import SkillManifest
from stackowl.skills.skill_md import parse_skill_md

_SHIPPED = Path(__file__).resolve().parents[2] / "src" / "stackowl" / "skills" / "_builtin"


def _shipped_skill_files() -> list[Path]:
    """THE POPULATION IS THE DIRECTORY, not a list someone remembered to update."""
    return sorted(_SHIPPED.glob("*/SKILL.md"))


@pytest.mark.tripwire
def test_every_shipped_skill_parses_and_yields_a_manifest() -> None:
    """The property, over the whole shipped set.

    `web-automation` failed exactly here on 2026-09-06 and reached users as a skill that
    silently did not exist.
    """
    broken: list[str] = []
    for path in _shipped_skill_files():
        try:
            parsed = parse_skill_md(path.read_text(encoding="utf-8"))
            SkillManifest(
                **{k: v for k, v in parsed.frontmatter.items() if k != "source"}
            )
        except Exception as exc:  # noqa: BLE001 — the failure IS the report
            broken.append(f"{path.parent.name}: {type(exc).__name__}: {exc}")

    assert not broken, (
        "these skills SHIP and cannot be loaded, so they are absent from every "
        "install's catalogue:\n  " + "\n  ".join(broken)
    )


@pytest.mark.tripwire
def test_the_population_is_enumerated_and_not_small() -> None:
    """VACUITY CONTROL, and it is the whole point of this file rather than a formality:
    a glob that resolves to nothing passes the assertion above in silence, which is the
    same failure mode as the name list it replaces."""
    files = _shipped_skill_files()

    assert len(files) >= 14, f"only {len(files)} shipped skills found under {_SHIPPED}"
    assert _SHIPPED.is_dir(), _SHIPPED


def test_it_covers_the_skill_the_name_lists_miss() -> None:
    """The live instance, pinned by name so the gap this file closes stays legible.

    If `chunked-pdf-summary` is ever added to a tier journey this assertion still holds —
    it asserts THIS test covers it, not that no other test does.
    """
    names = {p.parent.name for p in _shipped_skill_files()}

    assert "chunked-pdf-summary" in names
    assert "web-automation" in names, "the skill that actually broke is not in the sweep"
