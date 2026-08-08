"""D09.3 slice 4 — collapsing the ``-N`` families.

The shapes tested here are the ones actually in the live catalog on 2026-08-08,
not invented ones: 43 families, 312 rows inside them, 269 removable, and the
largest family (``recover_tool_search_unachieved_effect``, 21 members) has ZERO
executions across every member. That last fact is why the "nobody used any of
them" branch of the election gets as much attention here as the most-used one —
it is the common case, not the edge case.

This is the only irreversible operation in the skill lifecycle, so the tests
that matter most are the ones proving it does NOT act: dry-run by default, and
never delete what could not be archived first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.skills.consolidation import SkillConsolidator
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore

pytestmark = pytest.mark.asyncio

_STAMP = "20260808-120000"


async def _add(store: SkillIndexStore, root: Path, name: str, *,
               execs: int = 0, source: str = "learned") -> int:
    d = root / source / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n\nbody of {name}\n", encoding="utf-8")
    skill_id = await store.upsert(LoadedSkill(
        manifest=SkillManifest(
            name=name, description="d", when_to_use="w", source=source,  # type: ignore[arg-type]
        ),
        path=d, body=f"body of {name}", tools_registered=0, owls_registered=0,
    ))
    for _ in range(execs):
        await store.increment_n_executions(skill_id)
    return skill_id


def _consolidator(store: SkillIndexStore, root: Path) -> SkillConsolidator:
    return SkillConsolidator(store, root, archive_root=root.parent / "consolidated")


# --------------------------------------------------------------------------- #
# It must not act unless told to.
# --------------------------------------------------------------------------- #


async def test_the_default_is_a_dry_run(tmp_db, tmp_path):
    """THE test. Everything else in the skill lifecycle is reversible; this is
    not, so the default has to be 'tell me what you would do'."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson")
    await _add(store, tmp_path, "lesson-1")

    plan = await _consolidator(store, tmp_path).run(stamp=_STAMP)

    assert plan.applied is False
    assert plan.rows_removed == 1
    assert await store.get("learned", "lesson-1") is not None, "nothing was touched"
    assert (tmp_path / "learned" / "lesson-1").exists()


async def test_the_dry_run_and_the_apply_produce_the_same_plan(tmp_db, tmp_path):
    """A preview computed by different logic than the action previews nothing."""
    store = SkillIndexStore(tmp_db)
    for n, e in (("lesson", 0), ("lesson-1", 7), ("lesson-2", 2)):
        await _add(store, tmp_path, n, execs=e)

    preview = await _consolidator(store, tmp_path).run(stamp=_STAMP)
    applied = await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    assert [f.survivor for f in preview.families] == [f.survivor for f in applied.families]
    assert preview.rows_removed == applied.rows_removed


async def test_a_singleton_is_not_a_family(tmp_db, tmp_path):
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lonely")

    plan = await _consolidator(store, tmp_path).run(stamp=_STAMP)

    assert plan.families == []


# --------------------------------------------------------------------------- #
# Electing the survivor.
# --------------------------------------------------------------------------- #


async def test_the_most_used_member_survives_and_takes_the_base_name(tmp_db, tmp_path):
    """Identity matters, not just the name. After the pass exactly one row is
    called ``lesson`` and it is the row that used to be ``lesson-1`` — asserting
    only that ``lesson`` exists would pass even if the wrong member survived."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson", execs=1)
    winner_id = await _add(store, tmp_path, "lesson-1", execs=9)
    await _add(store, tmp_path, "lesson-2", execs=0)

    plan = await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    assert plan.families[0].survivor == "lesson-1"
    assert "most used" in plan.families[0].reason
    assert await store.get("learned", "lesson-1") is None, "renamed off its -N name"
    assert await store.get("learned", "lesson-2") is None

    survivor = await store.get("learned", "lesson")
    assert survivor is not None
    assert survivor.skill_id == winner_id, "the winner holds the base name, not a loser"
    assert (tmp_path / "learned" / "lesson" / "SKILL.md").read_text().endswith(
        "body of lesson-1\n",
    ), "the survivor's BODY moved, not just its row"
    assert not (tmp_path / "learned" / "lesson-1").exists()


async def test_the_survivor_inherits_the_summed_executions(tmp_db, tmp_path):
    """Without this the curator archives the survivor for looking unused, days
    after we merged it — a self-inflicted wound the two mechanisms would have
    dealt each other."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson", execs=3)
    await _add(store, tmp_path, "lesson-1", execs=9)
    await _add(store, tmp_path, "lesson-2", execs=4)

    await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    survivor = await store.get("learned", "lesson")
    assert survivor is not None
    assert survivor.n_executions == 16


async def test_a_pinned_member_wins_outright(tmp_db, tmp_path):
    """A human veto means the same thing here as everywhere else — even against
    a sibling with more executions."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson", execs=50)
    pinned_id = await _add(store, tmp_path, "lesson-1", execs=0)
    await store.set_pinned(pinned_id, True)

    plan = await _consolidator(store, tmp_path).run(stamp=_STAMP)

    assert plan.families[0].survivor == "lesson-1"
    assert plan.families[0].reason == "pinned"


async def test_two_pinned_members_make_the_family_untouchable(tmp_db, tmp_path):
    """Two vetoes cannot be resolved by picking one. Refusing is correct, and
    the refusal is REPORTED — a silent skip reads as 'nothing to do here'."""
    store = SkillIndexStore(tmp_db)
    a = await _add(store, tmp_path, "lesson")
    b = await _add(store, tmp_path, "lesson-1")
    await store.set_pinned(a, True)
    await store.set_pinned(b, True)

    plan = await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    assert plan.families == []
    assert len(plan.skipped) == 1
    assert "pinned" in plan.skipped[0]
    assert await store.get("learned", "lesson-1") is not None


async def test_an_entirely_unused_family_keeps_the_correctly_named_member(
    tmp_db, tmp_path,
):
    """The COMMON case, not the edge case: the live catalog's largest family has
    21 members and zero executions between them."""
    store = SkillIndexStore(tmp_db)
    for i in range(1, 21):
        await _add(store, tmp_path, f"recover_tool_search-{i}")
    await _add(store, tmp_path, "recover_tool_search")

    plan = await _consolidator(store, tmp_path).run(stamp=_STAMP)

    assert plan.families[0].survivor == "recover_tool_search"
    assert plan.families[0].reason == "already correctly named, none used"
    assert len(plan.families[0].removed) == 20
    assert plan.families[0].rename_needed is False


async def test_an_unused_family_with_no_base_member_is_still_deterministic(
    tmp_db, tmp_path,
):
    """Two runs over the same catalog must plan identically, or a dry run is not
    worth reading."""
    store = SkillIndexStore(tmp_db)
    for name in ("aaa-3", "aaa-1", "aaa-2"):
        await _add(store, tmp_path, name)

    first = await _consolidator(store, tmp_path).run(stamp=_STAMP)
    second = await _consolidator(store, tmp_path).run(stamp=_STAMP)

    assert first.families[0].survivor == second.families[0].survivor
    assert first.families[0].survivor == "aaa-1"


async def test_families_do_not_cross_sources(tmp_db, tmp_path):
    """A builtin and a learned skill may legitimately share a name; collapsing
    one onto the other would silently replace shipped behaviour."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "shared", source="builtin")
    await _add(store, tmp_path, "shared-1", source="learned")

    plan = await _consolidator(store, tmp_path).run(stamp=_STAMP)

    assert plan.families == []


# --------------------------------------------------------------------------- #
# The archive is what makes an irreversible operation survivable.
# --------------------------------------------------------------------------- #


async def test_removed_members_are_archived_before_deletion(tmp_db, tmp_path):
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson", execs=5)
    await _add(store, tmp_path, "lesson-1")

    plan = await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    assert plan.archive_path is not None
    archived = plan.archive_path / "lesson-1" / "SKILL.md"
    assert archived.exists()
    assert "body of lesson-1" in archived.read_text()


async def test_the_archive_lives_outside_the_catalog(tmp_db, tmp_path):
    """Inside it, the loader's ``_``-prefix skip rule would be the only thing
    stopping rediscovery — one convention away from resurrecting the lot."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson", execs=5)
    await _add(store, tmp_path, "lesson-1")

    plan = await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    assert plan.archive_path is not None
    assert tmp_path not in plan.archive_path.parents
    assert plan.archive_path.is_relative_to(tmp_path.parent / "consolidated")


async def test_a_member_that_cannot_be_archived_is_not_deleted(
    tmp_db, tmp_path, monkeypatch,
):
    """The archive is the entire safety model. Deleting anyway would trade it
    for tidiness — and the row would be gone with the files."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson", execs=5)
    await _add(store, tmp_path, "lesson-1")

    def _explode(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr("stackowl.skills.consolidation.shutil.copytree", _explode)

    await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    assert await store.get("learned", "lesson-1") is not None
    assert (tmp_path / "learned" / "lesson-1" / "SKILL.md").exists()


async def test_the_removed_files_are_gone_from_disk(tmp_db, tmp_path):
    """The operator asked for DELETED FROM DISK, not moved-and-hidden — the
    catalog is what the loader scans, and a hidden directory is still there."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson", execs=5)
    await _add(store, tmp_path, "lesson-1")

    await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    assert not (tmp_path / "learned" / "lesson-1").exists()


async def test_a_renamed_survivor_is_reachable_by_keyword_under_its_new_name(
    tmp_db, tmp_path,
):
    """skills_fts indexes the name. A rename that skipped the FTS sync would
    leave the survivor keyword-reachable only under the name it no longer has —
    the same half-wired shape as the archived-skill FTS gap in slice 3."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "zzqualifier")
    await _add(store, tmp_path, "zzqualifier-1", execs=9)

    await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    hits = await store.hybrid_recall("zzqualifier", [1.0, 0.0], limit=10)
    names = {sk.name for sk, _ in hits}
    assert "zzqualifier" in names
    assert "zzqualifier-1" not in names


# --------------------------------------------------------------------------- #
# A name lives in three places. Found in production by the conformance line.
# --------------------------------------------------------------------------- #


async def test_the_survivors_frontmatter_name_is_rewritten(tmp_db, tmp_path):
    """THE regression. Renaming the row and the directory but NOT the SKILL.md
    frontmatter let the next boot re-scan, read the stale `-N` name, and upsert
    a SECOND row pointing at the same directory — consolidation partially
    undoing itself overnight and resurrecting the numbered names it removed.

    Caught in production by the morning brief's conformance line, one boot after
    the first live consolidation, on 2 of the 43 families (the two that needed
    a rename at all).
    """
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson")
    await _add(store, tmp_path, "lesson-1", execs=9)

    await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    text = (tmp_path / "learned" / "lesson" / "SKILL.md").read_text()
    frontmatter = text.split("---")[1]
    assert "name: lesson\n" in frontmatter
    assert "lesson-1" not in frontmatter
    # SCOPE: the frontmatter only. The BODY still says "body of lesson-1" and
    # must — renaming a skill is not licence to rewrite what it says, and a
    # regex loose enough to touch prose is how a cleanup corrupts content.
    assert "body of lesson-1" in text


async def test_a_re_scan_after_consolidation_does_not_resurrect_the_old_name(
    tmp_db, tmp_path,
):
    """The actual production symptom, end to end: re-upsert from disk the way
    the loader does at boot, and confirm no second row appears."""
    store = SkillIndexStore(tmp_db)
    await _add(store, tmp_path, "lesson")
    await _add(store, tmp_path, "lesson-1", execs=9)
    await _consolidator(store, tmp_path).run(apply=True, stamp=_STAMP)

    # What the loader does on the next boot: read the directory, parse its
    # frontmatter, upsert.
    d = tmp_path / "learned" / "lesson"
    import yaml
    front = yaml.safe_load(d.joinpath("SKILL.md").read_text().split("---")[1])
    await store.upsert(LoadedSkill(
        manifest=SkillManifest(
            name=front["name"], description="d", when_to_use="w", source="learned",
        ),
        path=d, body="b", tools_registered=0, owls_registered=0,
    ))

    rows = await store.rows_for_curation()
    names = sorted(r.name for r in rows)
    assert names == ["lesson"], f"a numbered row came back: {names}"
