"""Deleting an owl removes its IDENTITY everywhere, and its HISTORY nowhere.

EARNED 2026-08-31, measured on the live database: the ``owls`` table held 10 rows
while ``owl_dna`` held 16 and ``owl_dna_authored`` 21. The six extras — Brain,
headhunter, jobmarket, newsdesk, sysdesign, sysfup — are owls that were deleted
and left their DNA behind.

The split was already known. This very package's ``test_owl_store`` docstring
records it from 2026-08-16: "one owl lived in four places — stackowl.yaml,
owl_dna (12), owl_dna_authored (17, SO 5 ORPHANS)". OwlStore was built to end
that split for WRITES. Deletion was never made to follow: ``OwlStore.delete``
removed the ``owls`` row and nothing else, and the DNA cleanup lived one layer up
in ``owls_command._delete_dna_rows`` — reachable only through ``/owls remove``, so
every other deletion path leaked.

AND THE LEAK PROPAGATED. GraphReconciliationHandler treats ``owl_dna`` and
``skill_ownership`` as AUTHORITATIVE and copies them into the Kuzu graph weekly.
It was working perfectly: graph Owl nodes == owl_dna == 16, graph Skill nodes ==
skill_ownership == 111 against 20 real skills. A healthy reconciler faithfully
republishing a shadow.

WHICH IS WHY THIS NEEDS NO GRAPH LEG. The reconciler PRUNES as well as backfills
(graph_reconciliation.py: delete_skill_node / delete_trait_node). Clean the SQLite
identity and the existing weekly loop heals the graph on its own — extending the
loop that exists rather than adding a second path that writes to Kuzu.

IDENTITY, NEVER HISTORY. Twelve tables carry ``owl_name``, but most are the record
of what the owl DID: cost_records (127k rows), task_outcomes, reflections,
conversations, tasks, sessions. Deleting those would destroy the evidence this
programme measures with. The precedent is ``SkillStore.delete``, which deliberately
does not touch ``skill_audit`` — and that restraint is exactly what made 128
purged skills recoverable on 2026-08-31.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.store import OwlStore
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


def _manifest(name: str) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name, role="generic", system_prompt="Be helpful.", model_tier="fast"
    )


@pytest.fixture
async def db(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """One pool per test, CLOSED at teardown.

    The first version of this file used a plain helper that opened a pool and
    never closed it. Outside pytest that is merely untidy; inside it the run
    HUNG and was killed at 300s — aiosqlite keeps a thread per connection, and
    the leaked ones never let the loop finish. A hanging test is a failing test,
    so this is a fixture rather than a helper. Mirrors tests/owls/test_owl_store.
    """
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "owls.db"
    pool = DbPool(db_path=path)
    await pool.open()
    seed_schema(path)
    yield pool
    await pool.close()


async def _seed_owl_everywhere(db: DbPool, name: str) -> None:
    """Put the owl in every place a real one lives."""
    await db.execute(
        "INSERT INTO owl_dna (owl_name, updated_at) VALUES (?, 1000.0)", (name,)
    )
    await db.execute(
        "INSERT INTO owl_dna_authored (owl_name, updated_at) VALUES (?, 1000.0)", (name,)
    )
    await db.execute(
        "INSERT INTO dna_checkpoints (owl_name, checkpoint_id, challenge_level, verbosity,"
        " curiosity, formality, creativity, precision, created_at)"
        " VALUES (?, ?, 1, 1, 1, 1, 1, 1, 1000.0)",
        # checkpoint_id is GLOBALLY unique, not per-owl — seeding two owls with
        # the same literal made the blast-radius test fail on the fixture rather
        # than on the code.
        (name, f"cp-{name}"),
    )
    await db.execute(
        "INSERT INTO skill_ownership (owner_id, owl_name, skill_name, attached_at)"
        " VALUES ('principal-default', ?, 'some-skill', 1000.0)",
        (name,),
    )
    # HISTORY — must survive the delete.
    await db.execute(
        "INSERT INTO conversations (id, session_key, owl_name, started_at)"
        " VALUES (?, 's-1', ?, '2026-08-31T00:00:00Z')",
        (f"conv-{name}", name),
    )


async def _count(db: DbPool, table: str, name: str) -> int:
    rows = await db.fetch_all(
        f"SELECT COUNT(*) AS c FROM {table} WHERE owl_name = ?", (name,)  # noqa: S608
    )
    return int(rows[0]["c"])


# =========================================================================== #
# 1. Identity goes
# =========================================================================== #


@pytest.mark.parametrize(
    "table", ["owl_dna", "owl_dna_authored", "dna_checkpoints", "skill_ownership"]
)
async def test_deleting_an_owl_removes_its_identity(db: DbPool, table: str) -> None:
    store = OwlStore(db)
    await store.upsert(_manifest("ghost"))
    await _seed_owl_everywhere(db, "ghost")
    assert await _count(db, table, "ghost") == 1

    assert await store.delete("ghost") is True

    assert await _count(db, table, "ghost") == 0, (
        f"{table} kept a row for a deleted owl — this is how six ghosts reached the graph"
    )


# =========================================================================== #
# 2. History stays
# =========================================================================== #


async def test_deleting_an_owl_keeps_the_record_of_what_it_did(db: DbPool) -> None:
    """The SkillStore precedent: skill_audit survives a skill, and that restraint
    is what made 128 purged skills recoverable."""
    store = OwlStore(db)
    await store.upsert(_manifest("ghost"))
    await _seed_owl_everywhere(db, "ghost")

    await store.delete("ghost")

    assert await _count(db, "conversations", "ghost") == 1, (
        "deleting an owl destroyed the history of what it did"
    )


# =========================================================================== #
# 3. Blast radius
# =========================================================================== #


async def test_another_owls_rows_are_untouched(db: DbPool) -> None:
    store = OwlStore(db)
    for name in ("ghost", "keeper"):
        await store.upsert(_manifest(name))
        await _seed_owl_everywhere(db, name)

    await store.delete("ghost")

    for table in ("owl_dna", "owl_dna_authored", "dna_checkpoints", "skill_ownership"):
        assert await _count(db, table, "keeper") == 1, f"{table} lost an unrelated owl"


async def test_deleting_an_owl_that_is_already_gone_is_a_clean_no_op(db: DbPool) -> None:
    """Idempotent, and it must not raise — restore_owl calls delete() to roll back
    a creation that may never have landed."""
    store = OwlStore(db)
    assert await store.delete("never-existed") is False


async def test_the_cascade_runs_even_when_only_the_shadows_remain(db: DbPool) -> None:
    """The state the live database is actually in: DNA rows for an owl whose
    `owls` row is already gone. Deleting again must still clear them, otherwise
    the six existing ghosts can never be cleaned through this path."""
    store = OwlStore(db)
    await _seed_owl_everywhere(db, "orphan")  # no owls row at all

    await store.delete("orphan")

    assert await _count(db, "owl_dna", "orphan") == 0
    assert await _count(db, "skill_ownership", "orphan") == 0
