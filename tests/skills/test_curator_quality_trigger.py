"""D09.3 X11 — ONE retirement mechanism, two triggers.

MIGRATED, not rewritten. Every test here was previously in
``test_skill_synthesizer.py`` driving ``SkillSynthesizer.deprecate_low_performers``.
The guarantees are unchanged — the same success rates, the same owl
``completion_drive`` arithmetic, the same orphaned-row degradation — because
the point of X11 was to move retirement, not to redesign when it fires.

WHAT DID CHANGE, deliberately, and what these tests now assert instead:

  * The outcome is ``lifecycle_state = 'archived'``, not a directory moved to
    ``learned/_deprecated/`` with the index row DELETED. The old path violated
    ADR-19 I3 outright: a skill retired for FAILING was unrecoverable while one
    retired for DISUSE was fully reversible, and nothing justified that.
  * The owl-drive advisory (AD-7 / Story 3.5) is injected as a threshold
    provider rather than computed inside the retiring class, so the curator
    stays owl-agnostic.
  * Pinning now protects a failing skill too. It never protected one before —
    the synthesizer path did not consult ``pinned`` at all, which meant a human
    veto covered exactly half of retirement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.skill_ownership import owl_drive_thresholds, persist_skill_ownership
from stackowl.skills.assembly import SkillsAssembly
from stackowl.skills.lifecycle import ARCHIVED, FAILING_BELOW, SkillCurator
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


async def _env(tmp_db: DbPool, tmp_path: Path, *, owl_dna: dict[str, float] | None = None):
    """A wired store plus an OwlRegistry carrying owls at given drives.

    Ported verbatim in intent from the old ``_deprecate_env`` so the migrated
    tests exercise the same fixture shape they were written against.
    """
    skills_root = tmp_path / "ws" / "skills"
    skills_root.mkdir(parents=True)
    registry = OwlRegistry.with_default_secretary()
    for owl_name, drive in (owl_dna or {}).items():
        registry.register(OwlAgentManifest(
            name=owl_name, role="research", system_prompt="P", model_tier="fast",
            dna=OwlDNA(completion_drive=drive),
        ))
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=registry,
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
    )
    return registry, skills_root, components.store


async def _seed(store, root: Path, name: str, *, success_rate: float,
                n_executions: int = 6, source: str = "learned") -> int:
    skill_dir = root / source / name
    skill_dir.mkdir(parents=True)
    body = f"# {name}\n"
    manifest = SkillManifest(
        name=name, description="d", when_to_use="w", source=source,  # type: ignore[arg-type]
    )
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\nwhen_to_use: w\nsource: {source}\n"
        f"---\n\n{body}", encoding="utf-8",
    )
    await store.upsert(LoadedSkill(
        manifest=manifest, path=skill_dir, body=body,
        tools_registered=0, owls_registered=0,
    ))
    sk = await store.get(source, name)
    assert sk is not None
    await store.set_success_rate(sk.skill_id, success_rate)
    for _ in range(n_executions):
        await store.increment_n_executions(sk.skill_id)
    return int(sk.skill_id)


async def _curate(store, registry: OwlRegistry, db: DbPool, *, use_owls: bool = True):
    """Run a real pass. The first-pass deferral is honoured by running twice —
    skipping it in tests would let a regression in the deferral itself hide."""
    provider = None
    if use_owls:
        async def provider() -> dict[str, float]:  # noqa: F811
            return await owl_drive_thresholds(db, registry, FAILING_BELOW)

    curator = SkillCurator(store, thresholds=provider)
    await curator.run()          # first observation — seeds the clock only
    return await curator.run()   # the pass under test


async def _state(store, name: str, source: str = "learned") -> str:
    rows = await store.rows_for_curation()
    row = next(r for r in rows if r.name == name and r.source == source)
    return row.lifecycle_state


# --------------------------------------------------------------------------- #
# The trigger itself.
# --------------------------------------------------------------------------- #


async def test_a_chronically_failing_skill_is_archived(tmp_db, tmp_path):
    """The behaviour ``deprecate_low_performers`` provided, now the curator's."""
    registry, root, store = await _env(tmp_db, tmp_path)
    await _seed(store, root, "bad-skill", success_rate=0.2)

    report = await _curate(store, registry, tmp_db, use_owls=False)

    assert "bad-skill" in report.to_archived
    assert "bad-skill" in report.archived_failing, "reported as failing, not merely unused"
    assert await _state(store, "bad-skill") == ARCHIVED


async def test_the_row_survives_archival(tmp_db, tmp_path):
    """ADR-19 I3, and the concrete regression this migration exists to fix: the
    old path DELETED the index row, so a wrongly-retired skill was gone."""
    registry, root, store = await _env(tmp_db, tmp_path)
    await _seed(store, root, "bad-skill", success_rate=0.2)

    await _curate(store, registry, tmp_db, use_owls=False)

    assert await store.get("learned", "bad-skill") is not None
    assert (root / "learned" / "bad-skill" / "SKILL.md").exists(), "nothing moved on disk"


async def test_too_few_runs_is_not_a_verdict(tmp_db, tmp_path):
    """A single early failure must not retire a skill. Same gate as before."""
    registry, root, store = await _env(tmp_db, tmp_path)
    await _seed(store, root, "young-skill", success_rate=0.0, n_executions=2)

    report = await _curate(store, registry, tmp_db, use_owls=False)

    assert "young-skill" not in report.to_archived


async def test_no_success_rate_is_never_failing(tmp_db, tmp_path):
    """``None`` means no verdict yet. Reading it as 0.0 would archive every
    skill that has run without ever being scored."""
    registry, root, store = await _env(tmp_db, tmp_path)
    root.joinpath("learned", "unscored").mkdir(parents=True)
    manifest = SkillManifest(
        name="unscored", description="d", when_to_use="w", source="learned",
    )
    (root / "learned" / "unscored" / "SKILL.md").write_text(
        "---\nname: unscored\n---\n\nx\n", encoding="utf-8",
    )
    await store.upsert(LoadedSkill(
        manifest=manifest, path=root / "learned" / "unscored", body="x",
        tools_registered=0, owls_registered=0,
    ))
    sk = await store.get("learned", "unscored")
    assert sk is not None
    for _ in range(9):
        await store.increment_n_executions(sk.skill_id)

    report = await _curate(store, registry, tmp_db, use_owls=False)

    assert "unscored" not in report.to_archived


async def test_a_pinned_failing_skill_is_spared(tmp_db, tmp_path):
    """NEW guarantee, and the reason one mechanism is better than two: the old
    deprecate path never consulted ``pinned``, so a human veto protected a
    skill from disuse but not from a bad success rate."""
    registry, root, store = await _env(tmp_db, tmp_path)
    skill_id = await _seed(store, root, "pinned-but-bad", success_rate=0.1)
    await store.set_pinned(skill_id, True)

    report = await _curate(store, registry, tmp_db, use_owls=False)

    assert "pinned-but-bad" not in report.to_archived
    assert report.skipped_pinned >= 1


async def test_using_an_archived_skill_revives_it(tmp_db, tmp_path):
    """What makes archival safe to be decisive about — and what the old
    move-the-directory path could not offer at all."""
    registry, root, store = await _env(tmp_db, tmp_path)
    skill_id = await _seed(store, root, "bad-skill", success_rate=0.2)
    await _curate(store, registry, tmp_db, use_owls=False)
    assert await _state(store, "bad-skill") == ARCHIVED

    await store.increment_n_executions(skill_id)

    assert await _state(store, "bad-skill") == "active"


# --------------------------------------------------------------------------- #
# AD-7 / Story 3.5 — the owl-drive advisory, migrated intact.
# --------------------------------------------------------------------------- #


async def test_high_completion_drive_owner_spares_a_borderline_skill(tmp_db, tmp_path):
    """drive=0.9 -> threshold 0.368. success_rate 0.38 is ABOVE the adjusted
    floor but BELOW the flat 0.4, so the nudge genuinely changed the outcome."""
    registry, root, store = await _env(tmp_db, tmp_path, owl_dna={"scout": 0.9})
    await _seed(store, root, "borderline-skill", success_rate=0.38)
    await persist_skill_ownership(tmp_db, "scout", "borderline-skill")

    report = await _curate(store, registry, tmp_db)

    assert "borderline-skill" not in report.to_archived


async def test_low_completion_drive_owner_retires_sooner(tmp_db, tmp_path):
    """drive=0.1 -> threshold 0.432. success_rate 0.42 is BELOW the adjusted
    floor but ABOVE the flat one — the nudge in the other direction."""
    registry, root, store = await _env(tmp_db, tmp_path, owl_dna={"scout": 0.1})
    await _seed(store, root, "impatient-owner-skill", success_rate=0.42)
    await persist_skill_ownership(tmp_db, "scout", "impatient-owner-skill")

    report = await _curate(store, registry, tmp_db)

    assert "impatient-owner-skill" in report.archived_failing


async def test_an_unowned_skill_uses_the_flat_floor(tmp_db, tmp_path):
    registry, root, store = await _env(tmp_db, tmp_path, owl_dna={"scout": 0.9})
    await _seed(store, root, "unowned-skill", success_rate=0.39)  # no ownership row

    report = await _curate(store, registry, tmp_db)

    assert "unowned-skill" in report.archived_failing


async def test_neutral_drive_is_identical_to_the_flat_floor(tmp_db, tmp_path):
    """drive=0.5 -> exactly FAILING_BELOW. The neutral default must not move."""
    registry, root, store = await _env(tmp_db, tmp_path, owl_dna={"scout": 0.5})
    await _seed(store, root, "neutral-owner-skill", success_rate=0.39)
    await persist_skill_ownership(tmp_db, "scout", "neutral-owner-skill")

    report = await _curate(store, registry, tmp_db)

    assert "neutral-owner-skill" in report.archived_failing


async def test_an_orphaned_ownership_row_degrades_without_aborting_the_pass(
    tmp_db, tmp_path,
):
    """A row naming an owl no longer in the registry degrades THAT skill to the
    flat floor — it must not raise, and must not cost other skills their
    thresholds."""
    registry, root, store = await _env(tmp_db, tmp_path, owl_dna={"scout": 0.9})
    await _seed(store, root, "orphan-owned-skill", success_rate=0.39)
    await persist_skill_ownership(tmp_db, "ghost", "orphan-owned-skill")  # never registered
    await _seed(store, root, "sibling-skill", success_rate=0.39)
    await persist_skill_ownership(tmp_db, "scout", "sibling-skill")

    report = await _curate(store, registry, tmp_db)

    # Orphan falls back to flat 0.4 (0.39 < 0.4 -> archived); the sibling's owner
    # (drive 0.9 -> 0.368) leaves 0.39 alone. Both processed, neither raised.
    assert "orphan-owned-skill" in report.archived_failing
    assert "sibling-skill" not in report.to_archived


async def test_a_raising_threshold_provider_costs_the_nudge_not_the_pass(
    tmp_db, tmp_path,
):
    """The advisory is additive weight, never a veto — so losing it must degrade
    to the flat floor rather than abandon decay entirely."""
    registry, root, store = await _env(tmp_db, tmp_path)
    await _seed(store, root, "bad-skill", success_rate=0.2)

    async def _explode() -> dict[str, float]:
        raise RuntimeError("ownership table is on fire")

    curator = SkillCurator(store, thresholds=_explode)
    await curator.run()
    report = await curator.run()

    assert "bad-skill" in report.archived_failing


# --------------------------------------------------------------------------- #
# Scope: built-ins decay too (R2Q6).
# --------------------------------------------------------------------------- #


async def test_a_failing_builtin_is_archived_too(tmp_db, tmp_path):
    """The operator's explicit reversal of the learned-only scope. Built-ins are
    reachable by the same rules; pinning is what protects one."""
    registry, root, store = await _env(tmp_db, tmp_path)
    await _seed(store, root, "bad-builtin", success_rate=0.1, source="builtin")

    report = await _curate(store, registry, tmp_db, use_owls=False)

    assert "bad-builtin" in report.to_archived
