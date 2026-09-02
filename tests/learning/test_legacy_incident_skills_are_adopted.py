"""The merge changed the WRITER and left the ROWS — so the corpus has both forms.

MEASURED 2026-08-31 on the live catalogue. ``learned`` holds 14 skills, and for
three capabilities it holds the same knowledge TWICE::

    incident_shell_stop                        3 runs   authored 08-30 03:04
    incident_shell_unachieved_effect           0 runs   authored 08-30 06:42
    incident_shell                             0 runs   authored 08-31 04:06

    incident_owl_build_stop                    3 runs   authored 08-30 04:40
    incident_owl_build                         0 runs   authored 08-31 03:48

    incident_delegate_task_unachieved_effect   2 runs   authored 08-30 03:18
    incident_delegate_task                     0 runs   authored 08-31 04:55

Commit 020b4145 (08-30 20:23) moved skill identity from
``incident_<capability>_<failure>`` to ``incident_<capability>``. The production
log shows both halves of the consequence, at INFO::

    01:11  author_one: skill already exists  existing_dir: incident_shell_stop
    04:06  author_one: exit — written + indexed    skill_name: incident_shell
    08:35  author_one: outcome already covered     existing_dir: incident_shell

Before the change the miner found the pair-form skill; after it, it looked for a
name that did not exist yet and wrote a THIRD copy of knowledge it already had.

WHY DECAY CANNOT CLEAN THIS UP, and why waiting is the wrong answer here. The
curator anchors idle time on ``last_used_at if n_executions > 0 else loaded_at``.
The legacy skills are the ones being SELECTED (``catalogue_order_key`` sorts by
``-n_executions``, so a 3-run duplicate outranks the 0-run survivor on every
turn), so their clock keeps resetting and they never go stale. The merged skill
has never been used, so it reaches STALE in 30 days. Left alone, the platform
archives the skill the miner MAINTAINS and keeps the two it has frozen.

WHAT ADOPTION DOES, AND WHAT IT DELIBERATELY DOES NOT. It transfers the legacy
run history to the capability's skill and archives the legacy row. It does NOT
fold the legacy prose across: every write to a skill body goes through
``gated_skill_write``, and the miner already has a merge path for new outcomes —
so the content is RE-DERIVED from live failures by the mechanism that exists,
rather than by a second writer that bypasses the gate. Archival is reversible
(one ``set_lifecycle_state``) and the SKILL.md stays on disk, so nothing is lost.
"""

from __future__ import annotations

import json
import time

import pytest

from stackowl.db.pool import DbPool
from stackowl.learning.failure_outcome_miner import (
    ADOPTED_LIFECYCLE_STATE,
    FailureOutcomeMiner,
    _canonical_incident_slug,
    legacy_siblings_for,
    merged_skill_name,
)
from stackowl.skills.store import SkillIndexStore
from tests._schema_template import seed_schema

# --------------------------------------------------------------- the name rule


def test_the_live_shape_both_shell_skills_are_legacy_siblings() -> None:
    """The exact catalogue measured above."""
    names = [
        "incident_shell", "incident_shell_stop", "incident_shell_unachieved_effect",
        "incident_owl_build", "incident_owl_build_stop",
    ]
    assert legacy_siblings_for("shell", names) == {
        "incident_shell_stop": "stop",
        "incident_shell_unachieved_effect": "unachieved_effect",
    }


def test_the_capabilitys_own_skill_is_never_its_own_sibling() -> None:
    """Adopting itself would archive the survivor — the failure mode that would
    leave the capability with no skill at all."""
    assert legacy_siblings_for("shell", ["incident_shell"]) == {}


def test_another_capabilitys_skill_is_untouched() -> None:
    assert legacy_siblings_for("shell", ["incident_owl_build_stop"]) == {}


def test_a_capability_whose_name_PREFIXES_another_does_not_swallow_it() -> None:
    """The one way a prefix rule can be wrong, closed with data rather than a
    vocabulary of failure classes.

    MEASURED: 26 distinct ``failed_capability`` values on the live database, and
    ZERO of them is a prefix of another. So the collision cannot happen today —
    but it costs nothing to make it structurally impossible, and a capability
    named ``shell_stop`` would otherwise have its OWN skill adopted by ``shell``.
    """
    names = ["incident_shell", "incident_shell_stop"]
    assert legacy_siblings_for(
        "shell", names, live_capabilities=("shell", "shell_stop"),
    ) == {}
    # ...and with no such capability in play it IS a legacy sibling.
    assert legacy_siblings_for("shell", names, live_capabilities=("shell",)) == {
        "incident_shell_stop": "stop",
    }


def test_the_base_name_asks_the_miners_own_identity_formula() -> None:
    """Two copies of one rule is the shape this whole item is about. The
    adoption pass must not restate the slug formula — it must ask it."""
    assert merged_skill_name("shell") == _canonical_incident_slug("shell", "stop")
    assert merged_skill_name("owl_build") == _canonical_incident_slug("owl_build", "")


def test_a_name_that_is_only_the_prefix_with_a_trailing_underscore_is_ignored() -> None:
    """``incident_shell_`` carries no failure class, so it names nothing."""
    assert legacy_siblings_for("shell", ["incident_shell_"]) == {}


# --------------------------------------------------------------- the adoption


@pytest.fixture
async def store(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "adopt.db"
    pool = DbPool(db_path=path)
    await pool.open()
    seed_schema(path)
    yield SkillIndexStore(pool), pool
    await pool.close()


async def _seed(pool: DbPool, owner: str, name: str, runs: int, *,
                category: str = "incident", state: str = "active",
                superseded_by: str | None = None) -> None:
    manifest = json.dumps({"name": name, "category": category})
    await pool.execute(
        "INSERT INTO skills (name, source, path, description, when_to_use,"
        " body_text, manifest_json, n_executions, lifecycle_state, owner_id,"
        " loaded_at, updated_at, superseded_by)"
        " VALUES (?, 'learned', ?, 'd', 'w', 'b', ?, ?, ?, ?, ?, ?, ?)",
        (name, f"/skills/learned/{name}", manifest, runs, state, owner,
         time.time(), time.time(), superseded_by),
    )


def _miner(index: SkillIndexStore, tmp_path) -> FailureOutcomeMiner:  # noqa: ANN001
    return FailureOutcomeMiner(
        outcome_store=None,  # type: ignore[arg-type]  # adoption never reads outcomes
        skill_store=index,
        skills_root=tmp_path,
    )


async def _row(pool: DbPool, name: str) -> dict:
    rows = await pool.fetch_all(
        "SELECT n_executions, lifecycle_state, superseded_by FROM skills WHERE name = ?",
        (name,),
    )
    return dict(rows[0])


@pytest.mark.asyncio
async def test_the_legacy_run_history_moves_to_the_capabilitys_skill(store, tmp_path) -> None:  # noqa: ANN001
    """The live shell case. Without the transfer the survivor looks unused and
    the curator archives it in 30 days — keeping the duplicates instead."""
    index, pool = store
    owner = index._owner_id  # noqa: SLF001 — the fixture must write the store's own owner
    await _seed(pool, owner, "incident_shell", 0)
    await _seed(pool, owner, "incident_shell_stop", 3)
    await _seed(pool, owner, "incident_shell_unachieved_effect", 0)

    adopted = await _miner(index, tmp_path).adopt_legacy_siblings({"shell"})

    assert adopted == 2
    assert (await _row(pool, "incident_shell"))["n_executions"] == 3
    assert (await _row(pool, "incident_shell_stop"))["lifecycle_state"] == (
        ADOPTED_LIFECYCLE_STATE
    )
    assert (await _row(pool, "incident_shell_unachieved_effect"))["lifecycle_state"] == (
        ADOPTED_LIFECYCLE_STATE
    )


@pytest.mark.asyncio
async def test_runs_from_SEVERAL_siblings_all_land(store, tmp_path) -> None:  # noqa: ANN001
    """A running total, not a snapshot read once — otherwise the second sibling
    overwrites the first's contribution instead of adding to it."""
    index, pool = store
    owner = index._owner_id  # noqa: SLF001
    await _seed(pool, owner, "incident_shell", 1)
    await _seed(pool, owner, "incident_shell_stop", 3)
    await _seed(pool, owner, "incident_shell_unachieved_effect", 5)

    await _miner(index, tmp_path).adopt_legacy_siblings({"shell"})

    assert (await _row(pool, "incident_shell"))["n_executions"] == 9


@pytest.mark.asyncio
async def test_it_is_idempotent(store, tmp_path) -> None:  # noqa: ANN001
    """The miner runs every ~12 minutes (80 passes on 2026-08-31). A second
    pass must not double-count the runs it already moved."""
    index, pool = store
    owner = index._owner_id  # noqa: SLF001
    await _seed(pool, owner, "incident_shell", 0)
    await _seed(pool, owner, "incident_shell_stop", 3)
    miner = _miner(index, tmp_path)

    assert await miner.adopt_legacy_siblings({"shell"}) == 1
    assert await miner.adopt_legacy_siblings({"shell"}) == 0
    assert (await _row(pool, "incident_shell"))["n_executions"] == 3


@pytest.mark.asyncio
async def test_a_capability_with_no_merged_skill_adopts_NOTHING(store, tmp_path) -> None:  # noqa: ANN001
    """browser_navigate today: one legacy skill and no survivor to adopt into.
    Archiving it here would remove the only skill the capability has."""
    index, pool = store
    owner = index._owner_id  # noqa: SLF001
    await _seed(pool, owner, "incident_browser_navigate_stop", 1)

    adopted = await _miner(index, tmp_path).adopt_legacy_siblings({"browser_navigate"})

    assert adopted == 0
    assert (await _row(pool, "incident_browser_navigate_stop"))["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_a_learned_skill_that_is_not_an_INCIDENT_skill_is_left_alone(store, tmp_path) -> None:  # noqa: ANN001
    """The prefix is a name rule; ``category`` is what says the miner wrote it.
    A hand-authored skill that happens to share the prefix is not the miner's
    to archive."""
    index, pool = store
    owner = index._owner_id  # noqa: SLF001
    await _seed(pool, owner, "incident_shell", 0)
    await _seed(pool, owner, "incident_shell_notes", 4, category=None)

    adopted = await _miner(index, tmp_path).adopt_legacy_siblings({"shell"})

    assert adopted == 0
    assert (await _row(pool, "incident_shell_notes"))["lifecycle_state"] == "active"
    assert (await _row(pool, "incident_shell"))["n_executions"] == 0


@pytest.mark.asyncio
async def test_an_already_FOLDED_sibling_is_not_re_adopted(store, tmp_path) -> None:  # noqa: ANN001
    """The idempotency guard reads ``superseded_by``, which has ONE writer."""
    index, pool = store
    owner = index._owner_id  # noqa: SLF001
    await _seed(pool, owner, "incident_shell", 3)
    await _seed(pool, owner, "incident_shell_stop", 3,
                state=ADOPTED_LIFECYCLE_STATE, superseded_by="incident_shell")

    assert await _miner(index, tmp_path).adopt_legacy_siblings({"shell"}) == 0
    assert (await _row(pool, "incident_shell"))["n_executions"] == 3


@pytest.mark.asyncio
async def test_a_REVIVED_sibling_does_not_get_its_runs_CREDITED_TWICE(  # noqa: ANN001
    store, tmp_path,
) -> None:
    """The production loop, reproduced.

    THIS TEST EXISTS BECAUSE THE OLD GUARD READ ``lifecycle_state``, and that
    column is owned by ``SkillCurator``, which revives anything whose idle clock
    is short. A sibling folded seconds ago is freshly loaded, so the revival was
    not a race — it was certain. MEASURED on the live platform:

        2026-08-31 15:49  miner folds 4 siblings, marks them archived
        2026-09-01 09:00  "[curator] run: exit ... revived 5"
        2026-09-01 23:09  miner folds the SAME siblings — incident_shell 3 -> 6,
                          incident_web_fetch 3 -> 6, incident_owl_build 4 -> 7
        2026-09-02 09:00  "[curator] run: exit ... revived 5"

    ``n_executions`` is what ``catalogue_order_key`` sorts by, so the loop was
    inflating the catalogue's ranking signal once a day, for ever.

    The sibling below is back to 'active' — exactly what the curator leaves
    behind — and must STILL not be re-credited.
    """
    index, pool = store
    owner = index._owner_id  # noqa: SLF001
    await _seed(pool, owner, "incident_shell", 6)
    await _seed(pool, owner, "incident_shell_stop", 3,
                state="active", superseded_by="incident_shell")

    assert await _miner(index, tmp_path).adopt_legacy_siblings({"shell"}) == 0
    assert (await _row(pool, "incident_shell"))["n_executions"] == 6


@pytest.mark.asyncio
async def test_folding_RECORDS_the_supersession_not_only_the_state(  # noqa: ANN001
    store, tmp_path,
) -> None:
    """Both writes, or the guard above is decoration.

    The state takes the duplicate out of the catalogue today; the column is what
    makes the fold survive a curator pass. Writing only one is how this class of
    bug is made — it is how it WAS made.
    """
    index, pool = store
    owner = index._owner_id  # noqa: SLF001
    await _seed(pool, owner, "incident_shell", 0)
    await _seed(pool, owner, "incident_shell_stop", 3)

    assert await _miner(index, tmp_path).adopt_legacy_siblings({"shell"}) == 1
    legacy = await _row(pool, "incident_shell_stop")
    assert legacy["superseded_by"] == "incident_shell"
    assert legacy["lifecycle_state"] == ADOPTED_LIFECYCLE_STATE


@pytest.mark.asyncio
async def test_a_broken_read_costs_the_mining_pass_NOTHING(store, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Adoption is hygiene. It must never be able to stop the miner from
    authoring the skill the incident actually needs."""
    index, pool = store
    owner = index._owner_id  # noqa: SLF001
    await _seed(pool, owner, "incident_shell", 0)

    async def _boom(*a: object, **k: object) -> list:
        raise RuntimeError("catalogue read failed")

    monkeypatch.setattr(index, "list_for_source", _boom)
    assert await _miner(index, tmp_path).adopt_legacy_siblings({"shell"}) == 0
