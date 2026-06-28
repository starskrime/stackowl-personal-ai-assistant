"""TurnDecisionStore (ADR-7) — durable round-trip of the per-turn DecisionLedger."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.decision_ledger import Decision
from stackowl.pipeline.decision_store import TurnDecisionStore


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "decisions.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_save_then_latest_roundtrips(pool: DbPool) -> None:
    store = TurnDecisionStore(pool)
    decisions = (
        Decision(point="router", verdict="act", reason="task detected"),
        Decision(
            point="acceptance",
            verdict="accepted",
            reason="postcondition met",
            inputs={"attempt": 1, "path": Path("/x")},  # non-str values
            alternatives_considered=("retry", "surrender"),
            evidence={"verified": True, "score": 0.91},  # non-str values
        ),
    )
    await store.save(session_id="s-1", trace_id="t-1", decisions=decisions)
    loaded = await store.latest("s-1")
    assert loaded is not None
    assert len(loaded) == 2
    assert loaded[0].point == "router"
    assert loaded[0].verdict == "act"
    assert loaded[1].alternatives_considered == ("retry", "surrender")
    # default=str serialization: non-str values survive as their str form.
    assert loaded[1].inputs["attempt"] == 1
    assert loaded[1].inputs["path"] == "/x"
    assert loaded[1].evidence["verified"] is True
    assert loaded[1].evidence["score"] == 0.91


async def test_save_upserts_latest_only(pool: DbPool) -> None:
    store = TurnDecisionStore(pool)
    await store.save(
        session_id="s-2", trace_id="t-a",
        decisions=(Decision(point="router", verdict="ask"),),
    )
    await store.save(
        session_id="s-2", trace_id="t-b",
        decisions=(Decision(point="router", verdict="act"),),
    )
    loaded = await store.latest("s-2")
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].verdict == "act"  # latest turn won


async def test_latest_unknown_session_returns_none(pool: DbPool) -> None:
    store = TurnDecisionStore(pool)
    assert await store.latest("never-seen") is None
