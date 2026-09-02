"""ADR-19 intervention #1 — the DECAY leg of the self-improvement loop.

Measured 2026-08-05 on the live catalog: 421 skills, 33 ever executed (7.8%),
0 ever retired, and 265 of 407 learned skills are numbered duplicates of an
existing base name. An improvement loop with no decay poisons its own signal.

The tests that matter most are the SAFETY ones — a curator that can quietly
retire a working capability is worse than no curator.
"""

from pathlib import Path

import pytest

from stackowl.skills.lifecycle import ACTIVE, ARCHIVED, STALE, SkillCurator
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore

_DAY = 86_400.0
_NOW = 1_800_000_000.0


def _loaded(name: str, source: str = "learned") -> LoadedSkill:
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source=source),
        path=Path("/tmp/x"), body="b", tools_registered=0, owls_registered=0, tool_names=(),
    )


async def _add(store: SkillIndexStore, name: str, *, age_days: float,
               source: str = "learned", execs: int = 0) -> int:
    """Insert a skill and backdate its clocks so age is expressible."""
    sid = await store.upsert(_loaded(name, source))
    anchor = _NOW - age_days * _DAY
    await store._db.execute(
        "UPDATE skills SET loaded_at = ?, n_executions = ?, "
        "last_used_at = CASE WHEN ? > 0 THEN ? ELSE NULL END WHERE skill_id = ?",
        (anchor, execs, execs, anchor, sid),
    )
    return sid


async def _curated(store: SkillIndexStore, **kw):
    """Run a real pass, past the deliberate first-run deferral."""
    curator = SkillCurator(store, **kw)
    await curator.run(now=_NOW)          # deferred: seeds the clock only
    return await curator.run(now=_NOW)   # the pass that acts


async def _state(store: SkillIndexStore, name: str) -> str:
    rows = await store._db.fetch_all(
        "SELECT lifecycle_state FROM skills WHERE name = ?", (name,)
    )
    return str(rows[0]["lifecycle_state"])


# --------------------------------------------------------------------------- #
# Safety. These are the point.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_first_pass_changes_NOTHING(tmp_db):
    """On a catalog that has never been curated every unused skill is eligible
    at once, so the first pass would be the largest change the curator ever
    makes — taken before anyone could pin anything."""
    store = SkillIndexStore(tmp_db)
    await _add(store, "ancient", age_days=500)

    report = await SkillCurator(store).run(now=_NOW)

    assert report.deferred
    assert report.changed == 0
    assert await _state(store, "ancient") == ACTIVE


@pytest.mark.asyncio
async def test_a_pinned_skill_is_never_touched(tmp_db):
    """The human veto outranks every automatic transition (ADR-19 I4)."""
    store = SkillIndexStore(tmp_db)
    sid = await _add(store, "precious", age_days=500)
    await store.set_pinned(sid, True)

    report = await _curated(store)

    assert await _state(store, "precious") == ACTIVE
    assert report.skipped_pinned == 1


@pytest.mark.asyncio
async def test_builtin_skills_decay_on_the_same_windows(tmp_db):
    """REVERSED in D09.3 (R2Q6), and the inversion is the point of the test.

    This suite previously asserted that a built-in NEVER decays, on the grounds
    that archiving one would be disabling a feature nobody asked to disable. The
    operator asked: 9 of 14 shipped built-ins had never once run, so the
    exclusion meant the shipped shelf could only ever grow, and the
    never-disable rule was protecting dead weight rather than a capability.

    The rule is satisfied by consent plus reversibility, not by exemption —
    which is what the next test pins down.
    """
    store = SkillIndexStore(tmp_db)
    await _add(store, "shipped", age_days=500, source="builtin")

    report = await _curated(store)

    assert await _state(store, "shipped") == ARCHIVED
    assert "shipped" in report.to_archived


@pytest.mark.asyncio
async def test_pinning_is_what_protects_a_builtin_now(tmp_db):
    """The replacement for the blanket exemption. A built-in that genuinely must
    never be retired is pinned, which is a decision someone made and can see —
    unlike a source check buried in a WHERE clause."""
    store = SkillIndexStore(tmp_db)
    sid = await _add(store, "load-bearing", age_days=500, source="builtin")
    await store.set_pinned(sid, True)

    report = await _curated(store)

    assert await _state(store, "load-bearing") == ACTIVE
    assert report.skipped_pinned == 1


@pytest.mark.asyncio
async def test_nothing_is_ever_deleted(tmp_db):
    """Archive is terminal AND recoverable (ADR-19 I3). The row keeps its body."""
    store = SkillIndexStore(tmp_db)
    await _add(store, "gone", age_days=500)

    await _curated(store)

    rows = await tmp_db.fetch_all(
        "SELECT lifecycle_state, body_text FROM skills WHERE name = 'gone'"
    )
    assert len(rows) == 1, "archival must never delete the row"
    assert rows[0]["lifecycle_state"] == ARCHIVED
    assert rows[0]["body_text"] == "b", "the body must survive archival"


def test_a_skill_with_no_clock_is_never_aged():
    """A missing timestamp must not read as "infinitely old" — that would retire
    on a data defect rather than on evidence.

    Unit-level on purpose: `skills.loaded_at` is NOT NULL, so this cannot be
    reached through SQL today. The guard exists for the projection path, and
    testing it through a schema that forbids the input would have been a test
    that cannot fail — the trap this programme has paid for before.
    """
    from stackowl.skills.lifecycle import _CurationRow

    clockless = _CurationRow(
        skill_id=1, name="clockless", lifecycle_state=ACTIVE, pinned=False,
        n_executions=0, last_used_at=None, loaded_at=None,
    )
    curator = SkillCurator.__new__(SkillCurator)
    assert curator._idle_seconds(clockless, _NOW) == 0.0


# --------------------------------------------------------------------------- #
# The transitions themselves.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_unused_skill_goes_stale_then_archived(tmp_db):
    store = SkillIndexStore(tmp_db)
    await _add(store, "fresh", age_days=1)
    await _add(store, "quiet", age_days=45)
    await _add(store, "forgotten", age_days=200)

    report = await _curated(store)

    assert await _state(store, "fresh") == ACTIVE
    assert await _state(store, "quiet") == STALE
    assert await _state(store, "forgotten") == ARCHIVED
    assert report.to_stale == ["quiet"]
    assert report.to_archived == ["forgotten"]


@pytest.mark.asyncio
async def test_a_used_skill_ages_from_its_LAST_USE_not_its_creation(tmp_db):
    """Otherwise a skill created long ago and used daily would be retired."""
    store = SkillIndexStore(tmp_db)
    sid = await _add(store, "workhorse", age_days=500, execs=50)
    await tmp_db.execute(
        "UPDATE skills SET last_used_at = ? WHERE skill_id = ?", (_NOW - _DAY, sid)
    )

    await _curated(store)

    assert await _state(store, "workhorse") == ACTIVE


@pytest.mark.asyncio
async def test_using_an_archived_skill_revives_it(tmp_db):
    """What makes archival safe to be decisive about: a wrong retirement costs
    one ranking penalty, not a lost capability."""
    store = SkillIndexStore(tmp_db)
    sid = await _add(store, "comeback", age_days=500)
    await _curated(store)
    assert await _state(store, "comeback") == ARCHIVED

    await store.increment_n_executions(sid)

    assert await _state(store, "comeback") == ACTIVE


@pytest.mark.asyncio
async def test_pinning_revives_an_archived_skill(tmp_db):
    """Pinning something archived can only mean 'this should not have been
    retired'."""
    store = SkillIndexStore(tmp_db)
    sid = await _add(store, "rescued", age_days=500)
    await _curated(store)
    assert await _state(store, "rescued") == ARCHIVED

    await store.set_pinned(sid, True)

    assert await _state(store, "rescued") == ACTIVE


@pytest.mark.asyncio
async def test_a_dry_run_changes_nothing_but_reports_everything(tmp_db):
    store = SkillIndexStore(tmp_db)
    await _add(store, "doomed", age_days=200)
    await SkillCurator(store).run(now=_NOW)  # clear the deferral

    report = await SkillCurator(store).run(dry_run=True, now=_NOW)

    assert report.to_archived == ["doomed"]
    assert await _state(store, "doomed") == ACTIVE, "dry run must not mutate"


@pytest.mark.asyncio
async def test_inverted_windows_cannot_archive_in_the_same_pass_as_stale(tmp_db):
    """A misconfiguration must not turn a reversible signal into an
    irreversible-feeling one with no warning period."""
    store = SkillIndexStore(tmp_db)
    await _add(store, "victim", age_days=40)

    report = await _curated(store, stale_after_days=30, archive_after_days=10)

    assert report.to_archived == []
    assert await _state(store, "victim") == STALE


# --------------------------------------------------------------------------- #
# Wiring: decay only pays off if retrieval respects it.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_archived_skills_are_not_offered_but_remain_in_the_database(tmp_db):
    """The entire performance case for this work. If list_enabled still returns
    them, 92% of the catalog keeps costing prompt space."""
    store = SkillIndexStore(tmp_db)
    await _add(store, "live", age_days=1)
    await _add(store, "retired", age_days=200)
    await _curated(store)

    offered = {s.name for s in await store.list_enabled()}

    assert offered == {"live"}
    still_there = await tmp_db.fetch_all("SELECT name FROM skills WHERE name = 'retired'")
    assert len(still_there) == 1, "not offered is not the same as deleted"


@pytest.mark.asyncio
async def test_stale_skills_are_still_offered(tmp_db):
    """Stale is a ranking signal, not a removal — a stale skill that is the best
    match for a query should still win against nothing."""
    store = SkillIndexStore(tmp_db)
    await _add(store, "quiet", age_days=45)
    await _curated(store)
    assert await _state(store, "quiet") == STALE

    assert {s.name for s in await store.list_enabled()} == {"quiet"}


@pytest.mark.asyncio
async def test_lifecycle_counts_reports_the_catalog(tmp_db):
    store = SkillIndexStore(tmp_db)
    await _add(store, "a", age_days=1)
    await _add(store, "b", age_days=45)
    await _add(store, "c", age_days=200)
    await _curated(store)

    assert await store.lifecycle_counts() == {ACTIVE: 1, STALE: 1, ARCHIVED: 1}


# --------------------------------------------------------------------------- #
# Supersession — the one archival that use may NOT undo.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_SUPERSEDED_skill_is_never_revived(tmp_db):
    """The bug this branch exists for, reproduced from production.

    ``FailureOutcomeMiner.adopt_legacy_siblings`` folds
    ``incident_<capability>_<failure>`` into ``incident_<capability>``, moves the
    run history across, and archives the loser. The curator owned the same column
    and revived anything whose idle clock was short — and a skill folded seconds
    ago is freshly loaded, so its clock is ALWAYS short. The revival was not a
    race; it was certain. MEASURED:

        2026-08-31 15:49  miner folds 4 siblings
        2026-09-01 09:00  "[curator] run: exit ... revived 5"
        2026-09-01 23:09  miner folds the SAME 4 again, re-crediting their runs
        2026-09-02 09:00  "[curator] run: exit ... revived 5"

    Note what the fixture does: this skill is one day old with a recent run, so
    every ordinary rule in ``_target_state`` says ACTIVE. Only supersession keeps
    it archived — which is what makes the assertion mean something.
    """
    store = SkillIndexStore(tmp_db)
    sid = await _add(store, "incident_shell_stop", age_days=1, execs=3)
    await store.set_superseded_by(sid, "incident_shell")

    await _curated(store)

    assert await _state(store, "incident_shell_stop") == ARCHIVED


@pytest.mark.asyncio
async def test_a_skill_that_stands_on_its_own_is_untouched_by_the_new_branch(tmp_db):
    """The control. Without it the test above passes for a curator that archives
    everything, which is the failure mode of a one-sided guard."""
    store = SkillIndexStore(tmp_db)
    await _add(store, "incident_shell", age_days=1, execs=3)

    await _curated(store)

    assert await _state(store, "incident_shell") == ACTIVE
