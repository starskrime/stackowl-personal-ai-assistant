"""The skills we SHIP are held to the standard we wrote.

WHY THIS EXISTS. `standard.py` defines the authoring standard and
`gated_skill_write` enforces it — on WRITES. The 14 builtin skills under
`skills/_builtin/` were never a write: they are files in the repository, seeded to
disk at boot, so they enter the catalogue through the one door the validator does
not watch.

MEASURED 2026-09-06 by running the real validator over the shipped files: **all 14
carry blocking violations**, in three kinds — `description` (20), `sections` (14),
`section_order` (13). Every description exceeded the 60-character cap, from 102 to
**235** characters. The platform shipped a catalogue that violates its own standard
100%, while refusing exactly those writes from anyone else.

`SkillStandardMigrator` cannot help and is right not to: it excludes builtins
deliberately, because "those are shipped files under version control, and rewriting
them with an LLM would put generated content into the repository". So the only
place they can be fixed is here, by hand, in the repo — which is why this guard
lives beside them rather than in the migrator.

WHAT THIS ENFORCES AND WHAT IT DOES NOT. The description rule is enforced: the 14
files now conform (longest 56). The BODY rules — the seven required sections and
their order — are not, because 14 files still violate them and arming a rule the
tree fails would block every commit in the repo. That is DEBT-130's lesson applied
to my own guard: land the rule in the same change as the compliance, never before
it. The sections backlog is recorded in DEBT-137 with its measurement.

The descriptions were shortened rather than deleted, and nothing was lost: the
standard already gives the detail a home in `when_to_use`, which every one of these
files carries and which is not capped.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_BUILTIN = _ROOT / "src" / "stackowl" / "skills" / "_builtin"

sys.path.insert(0, str(_ROOT / "src"))
from stackowl.skills import standard as std  # noqa: E402


def _frontmatter(text: str) -> dict[str, object]:
    """Parse the frontmatter with REAL YAML, the way the loader does.

    THE FIRST VERSION SPLIT ON THE FIRST COLON, and that is how this guard passed
    a file the loader rejects. A description of "Drive a website: navigate, read,
    fill forms, extract." is invalid YAML — "mapping values are not allowed here" —
    so `load_all` logged "skill invalid — skipping" and the stale row survived,
    while this test read the description as "Drive a website" (16 chars) and called
    it conforming. A double that parses more leniently than the thing it stands for
    reports a green that means nothing; the fix is to use the same parser, not a
    better regex.
    """
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    loaded = yaml.safe_load(m.group(1))
    return loaded if isinstance(loaded, dict) else {}


def _shipped() -> list[tuple[str, dict[str, object]]]:
    out = []
    for d in sorted(_BUILTIN.iterdir()):
        p = d / "SKILL.md"
        if p.exists():
            out.append((d.name, _frontmatter(p.read_text(encoding="utf-8"))))
    return out


def _description_violations(fields: dict[str, object]) -> list[object]:
    """Only the description rule, asked of the REAL validator.

    Not a re-implementation of the 60-character cap: the whole defect is a rule
    enforced in one place and not another, and a second copy of the number here
    would be the same shape again.

    The field is ``rule``, not ``code``. The first draft filtered on ``.code``,
    which ``Violation`` does not have, so it would have returned an empty list for
    every skill and passed over nothing — caught by reading the dataclass rather
    than trusting a getattr chain that had silently fallen back to ``.rule``.
    """
    return [v for v in std.validate_frontmatter(fields) if v.rule == "description"]


class TestShippedDescriptionsConform:
    @pytest.mark.tripwire
    def test_no_shipped_skill_exceeds_the_description_cap(self) -> None:
        offenders = {
            name: [str(v)[:90] for v in _description_violations(fields)]
            for name, fields in _shipped()
            if _description_violations(fields)
        }

        assert not offenders, (
            "these SHIPPED skills violate the description rule the platform enforces "
            f"on everyone else's writes: {offenders}"
        )

    def test_the_guard_sees_the_real_catalogue(self) -> None:
        """VACUITY CONTROL. An empty directory or a frontmatter parser that returned
        nothing would pass the assertion above over an empty set."""
        shipped = _shipped()

        assert len(shipped) >= 10, f"only found {len(shipped)} shipped skills"
        assert all(f.get("description") for _n, f in shipped), (
            "a shipped skill has no description at all — the parser is broken"
        )

    @pytest.mark.tripwire
    def test_every_shipped_skill_is_valid_yaml(self) -> None:
        """The failure that actually bit: a file the loader REFUSES never reaches the
        catalogue at all, so its old row survives and every other check here passes
        over a skill that was silently skipped."""
        broken = {}
        for d in sorted(_BUILTIN.iterdir()):
            p = d / "SKILL.md"
            if not p.exists():
                continue
            m = re.match(r"^---\n(.*?)\n---\n", p.read_text(encoding="utf-8"), re.S)
            if m is None:
                broken[d.name] = "no frontmatter block"
                continue
            try:
                yaml.safe_load(m.group(1))
            except Exception as exc:  # noqa: BLE001 — the message is the point
                broken[d.name] = str(exc)[:90]

        assert not broken, f"the loader will skip these entirely: {broken}"

    def test_the_validator_would_still_reject_an_over_long_one(self) -> None:
        """The control in the other direction: the rule can fail, so a pass means
        something. Built from the standard's own constant, not a literal."""
        too_long = {"name": "x", "description": "y" * (std.MAX_DESCRIPTION_CHARS + 1)}

        assert _description_violations(too_long), "the description rule stopped firing"
