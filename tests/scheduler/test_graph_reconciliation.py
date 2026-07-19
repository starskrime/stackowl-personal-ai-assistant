"""GraphReconciliationHandler — diffs SQLite against the graph, backfills what's
missing, prunes what's stale. Per-item isolated (one bad row never stops the rest)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.kuzu_adapter import KuzuAdapter
from stackowl.scheduler.handlers.graph_reconciliation import GraphReconciliationHandler
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "recon.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture()
async def kuzu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[KuzuAdapter]:
    from stackowl.config.test_mode import TestModeGuard
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    yield adapter
    await adapter.aclose()


def _job() -> Job:
    return Job(
        job_id="graph_reconciliation-1", handler_name="graph_reconciliation",
        schedule="every 168h", idempotency_key="graph_reconciliation:every-168h",
        last_run_at=None, next_run_at="2026-01-01T00:00:00+00:00", status="running",
    )


async def test_backfills_missing_skill_ownership(db: DbPool, kuzu: KuzuAdapter) -> None:
    await db.execute(
        "INSERT INTO skill_ownership (owner_id, owl_name, skill_name, attached_at) "
        "VALUES (?, ?, ?, ?)",
        ("principal-default", "Brain", "web_search", 0.0),
    )
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    ids = await kuzu.list_skill_ids()
    assert ids == ["principal-default::web_search"]


async def test_backfills_missing_dna_traits(db: DbPool, kuzu: KuzuAdapter) -> None:
    from stackowl.owls.dna import OwlDNA
    from stackowl.owls.dna_storage import upsert_owl_dna

    await upsert_owl_dna(db, "Brain", OwlDNA(), table="owl_dna")
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    ids = await kuzu.list_trait_ids()
    assert len(ids) == 7  # one per TRAIT_NAMES entry


async def test_prunes_stale_skill_no_longer_in_sqlite(db: DbPool, kuzu: KuzuAdapter) -> None:
    await kuzu.upsert_skill_node("principal-default::gone", "principal-default", "gone")
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    ids = await kuzu.list_skill_ids()
    assert ids == []


async def test_no_kuzu_wired_is_a_clean_noop(db: DbPool) -> None:
    handler = GraphReconciliationHandler(db, None)

    result = await handler.execute(_job())

    assert result.success is True


async def test_unreachable_graph_degrades_to_noop_on_skill_fetch(
    db: DbPool, kuzu: KuzuAdapter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await db.execute(
        "INSERT INTO skill_ownership (owner_id, owl_name, skill_name, attached_at) "
        "VALUES (?, ?, ?, ?)",
        ("principal-default", "Brain", "web_search", 0.0),
    )

    async def _raise() -> list[str]:
        raise RuntimeError("kuzu unreachable")

    monkeypatch.setattr(kuzu, "list_skill_ids", _raise)
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    assert result.error is None


async def test_unreachable_graph_degrades_to_noop_on_trait_fetch(
    db: DbPool, kuzu: KuzuAdapter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackowl.owls.dna import OwlDNA
    from stackowl.owls.dna_storage import upsert_owl_dna

    await upsert_owl_dna(db, "Brain", OwlDNA(), table="owl_dna")

    async def _raise() -> list[str]:
        raise RuntimeError("kuzu unreachable")

    monkeypatch.setattr(kuzu, "list_trait_ids", _raise)
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    assert result.error is None


async def test_one_bad_row_does_not_stop_the_sweep(
    db: DbPool, kuzu: KuzuAdapter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await db.execute(
        "INSERT INTO skill_ownership (owner_id, owl_name, skill_name, attached_at) "
        "VALUES (?, ?, ?, ?)",
        ("principal-default", "Brain", "s1", 0.0),
    )
    await db.execute(
        "INSERT INTO skill_ownership (owner_id, owl_name, skill_name, attached_at) "
        "VALUES (?, ?, ?, ?)",
        ("principal-default", "Brain", "s2", 0.0),
    )
    original = kuzu.upsert_skill_node
    calls = {"n": 0}

    async def _flaky(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(kuzu, "upsert_skill_node", _flaky)
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    ids = await kuzu.list_skill_ids()
    assert len(ids) == 1  # the second row still got synced despite the first raising
