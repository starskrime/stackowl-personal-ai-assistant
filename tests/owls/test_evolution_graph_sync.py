"""EvolutionCoordinator's best-effort DNA graph sync — a Kuzu failure must
never affect the durable (SQLite) persist outcome or raise."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_hydrator import read_all_owl_dna
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.owls.registry import OwlRegistry

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "evo.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def test_persist_dna_syncs_all_traits_to_graph(db: DbPool) -> None:
    kuzu = AsyncMock()
    coordinator = EvolutionCoordinator(
        db, provider_registry=AsyncMock(), owl_registry=OwlRegistry(), kuzu=kuzu,
    )

    await coordinator._persist_dna("Brain", OwlDNA())

    kuzu.upsert_owl_node.assert_awaited_once_with("Brain")
    assert kuzu.upsert_trait_node.await_count == 7  # one per TRAIT_NAMES entry
    assert kuzu.link_owl_has_trait.await_count == 7
    # the durable write still happened regardless of the graph mock
    persisted = await read_all_owl_dna(db)
    assert "Brain" in persisted


async def test_persist_dna_survives_graph_sync_failure(db: DbPool) -> None:
    kuzu = AsyncMock()
    kuzu.upsert_owl_node.side_effect = RuntimeError("kuzu down")
    coordinator = EvolutionCoordinator(
        db, provider_registry=AsyncMock(), owl_registry=OwlRegistry(), kuzu=kuzu,
    )

    await coordinator._persist_dna("Brain", OwlDNA())  # must not raise

    persisted = await read_all_owl_dna(db)
    assert "Brain" in persisted  # SQLite write unaffected


async def test_persist_dna_with_no_kuzu_wired_still_works(db: DbPool) -> None:
    coordinator = EvolutionCoordinator(
        db, provider_registry=AsyncMock(), owl_registry=OwlRegistry(), kuzu=None,
    )

    await coordinator._persist_dna("Brain", OwlDNA())  # must not raise

    persisted = await read_all_owl_dna(db)
    assert "Brain" in persisted
