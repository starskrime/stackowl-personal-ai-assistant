"""A skill outside a source dir must be REPORTED, not silently skipped.

Measured 2026-09-04: 40 ``SKILL.md`` files sat under ``~/.stackowl/skills`` and
the last boot registered 39. The missing one — ``trending-research-owl/`` — had
been on disk since 2026-06-27 and had produced ZERO log lines in 69 days.

The loader's "never silent" guarantee (``skill invalid — skipping``) only ever
covered files it FOUND. ``load_all`` iterates ``_VALID_SOURCES`` and joins each
onto the root, so it enumerates the set of directories it EXPECTS. An iteration
over the expected set can never notice an unexpected member: the misplaced skill
was not rejected, it was never looked at.

So the fix is not to load it — ``source`` is a trust input (it is forced from the
directory precisely "so frontmatter can't lie", and ``_OWL_TRUSTED_SOURCES``
gates owl loading on it), and adopting an unknown directory would hand an
unplaceable skill a trust level nobody assigned it. The fix is that the loader
must be able to say what it did NOT load.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import get_args

import pytest

from stackowl.skills.loader import _VALID_SOURCES, SkillLoader
from stackowl.skills.manifest import SkillSource


def _write(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_a_skill_outside_a_source_dir_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(tmp_path / "user" / "placed", "placed")
    _write(tmp_path / "trending-research-owl", "trending_research_owl")

    with caplog.at_level(logging.WARNING, logger="stackowl.skills"):
        loaded = await SkillLoader().load_all(tmp_path, builtin_seed_dir=tmp_path / "none")

    names = {ls.manifest.name for ls in loaded}
    assert names == {"placed"}, "an unplaceable skill must NOT be adopted into a trust level"

    stray = [r for r in caplog.records if "outside any source dir" in r.getMessage()]
    assert stray, "the skill it did not load must appear in the log"
    assert "trending-research-owl" in str(getattr(stray[0], "_fields", {}))


@pytest.mark.asyncio
async def test_a_well_formed_tree_reports_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The control: the zero above has to be able to be non-zero, and vice versa."""
    _write(tmp_path / "user" / "placed", "placed")
    _write(tmp_path / "builtin" / "cat" / "nested", "nested")

    with caplog.at_level(logging.WARNING, logger="stackowl.skills"):
        loaded = await SkillLoader().load_all(tmp_path, builtin_seed_dir=tmp_path / "none")

    assert {ls.manifest.name for ls in loaded} == {"placed", "nested"}
    assert not [r for r in caplog.records if "outside any source dir" in r.getMessage()]


def test_a_non_skill_directory_is_not_reported(tmp_path: Path) -> None:
    """Only skill-SHAPED strays count. A bare directory is not a lost skill."""
    from stackowl.skills.loader import _stray_skill_dirs

    (tmp_path / "user").mkdir()
    (tmp_path / "notes").mkdir()
    _write(tmp_path / "orphan", "orphan")

    assert [p.name for p in _stray_skill_dirs(tmp_path)] == ["orphan"]


def test_no_module_restates_the_source_vocabulary() -> None:
    """There were FOUR copies, agreeing by luck: the manifest Literal, the
    loader tuple, the command tuple and the command's `choices=`. D10.1 collapsed
    two of them and said so; the other two were found in D10.4, which is why this
    now asserts over every module instead of the pair I happened to look at."""
    from stackowl.commands.skill_command import _VALID_SOURCES as command_sources

    assert command_sources == get_args(SkillSource)
    assert command_sources == _VALID_SOURCES


def test_the_valid_sources_are_one_definition_not_two() -> None:
    """The loader's tuple and the manifest's Literal were two copies of one rule.

    They agreed, which is the only reason this was cheap to fix rather than a
    defect: a source added to one and not the other loads skills the manifest
    cannot validate, or validates skills the loader will never scan.
    """
    assert _VALID_SOURCES == get_args(SkillSource)
