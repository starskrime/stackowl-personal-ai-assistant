"""ExplainCommand (ADR-7) — /explain reads the durable DecisionLedger snapshot."""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.explain_command import ExplainCommand
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.decision_ledger import Decision
from stackowl.pipeline.decision_store import TurnDecisionStore
from stackowl.pipeline.state import PipelineState


def _state(session: str = "sess-1") -> PipelineState:
    return PipelineState(
        trace_id="trace-1",
        session_id=session,
        input_text="why?",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


@pytest.fixture()
def _temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point DbPool()'s default path at a migrated temp DB for the command."""
    db_path = tmp_path / "explain.db"
    MigrationRunner(db_path=db_path).run()
    monkeypatch.setattr("stackowl.db.pool.default_db_path", lambda: db_path)
    return db_path


async def test_explain_renders_persisted_snapshot(_temp_db: Path) -> None:
    # Seed a snapshot through the store (same path the command reads).
    pool = DbPool(db_path=_temp_db)
    await pool.open()
    try:
        await TurnDecisionStore(pool).save(
            session_id="sess-1", trace_id="trace-1",
            decisions=(
                Decision(point="router", verdict="act", reason="task detected"),
                Decision(point="acceptance", verdict="accepted"),
            ),
        )
    finally:
        await pool.close()

    out = await ExplainCommand().handle("", _state("sess-1"))
    assert "router — act — task detected" in out
    assert "acceptance — accepted" in out


async def test_explain_empty_when_no_snapshot(_temp_db: Path) -> None:
    out = await ExplainCommand().handle("", _state("no-such-session"))
    assert out == "No decisions were recorded for your last turn."
