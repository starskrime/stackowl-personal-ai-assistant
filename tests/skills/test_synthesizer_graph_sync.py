"""SkillSynthesizer's best-effort graph sync on skill attach — a Kuzu failure
must never affect the durable (SQLite) attach outcome or raise."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcome
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.skills.synthesizer import SkillSynthesizer, ToolSequenceCluster

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "synth.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _cluster(owner: str) -> ToolSequenceCluster:
    outcome = TaskOutcome(
        outcome_id=1, trace_id="t1", session_id="s1", owl_name=owner,
        channel="cli", success=True, latency_ms=100.0, tool_call_count=1,
        failure_class=None, quality_score=0.9, step_durations={},
        input_text="do the thing", response_text="done",
        captured_at=0.0, scored_at=0.0, tool_sequence=("web_search",),
    )
    return ToolSequenceCluster(sequence=("web_search",), outcomes=(outcome,))


def _make_synth(db: DbPool, registry: OwlRegistry, kuzu: Any) -> SkillSynthesizer:
    return SkillSynthesizer(
        outcome_store=AsyncMock(), skill_store=AsyncMock(),
        provider=AsyncMock(), skills_root=Path("/tmp/unused"),
        owl_registry=registry, db=db, kuzu=kuzu,
    )


async def test_attach_syncs_graph_on_success(db: DbPool) -> None:
    registry = OwlRegistry()
    registry.register(OwlAgentManifest(
        name="Brain", role="assistant", system_prompt="You are Brain.",
        model_tier="fast", skills=(),
    ))
    kuzu = AsyncMock()
    synth = _make_synth(db, registry, kuzu)

    attached = await synth._attach_to_owner(_cluster("Brain"), "new_skill")

    assert attached is True
    kuzu.upsert_owl_node.assert_awaited_once_with("Brain")
    kuzu.upsert_skill_node.assert_awaited_once()
    kuzu.link_owl_owns_skill.assert_awaited_once()


async def test_attach_survives_graph_sync_failure(db: DbPool) -> None:
    registry = OwlRegistry()
    registry.register(OwlAgentManifest(
        name="Brain", role="assistant", system_prompt="You are Brain.",
        model_tier="fast", skills=(),
    ))
    kuzu = AsyncMock()
    kuzu.upsert_owl_node.side_effect = RuntimeError("kuzu down")
    synth = _make_synth(db, registry, kuzu)

    attached = await synth._attach_to_owner(_cluster("Brain"), "new_skill")

    assert attached is True  # the durable/live attach outcome is unaffected


async def test_attach_with_no_kuzu_wired_still_works(db: DbPool) -> None:
    registry = OwlRegistry()
    registry.register(OwlAgentManifest(
        name="Brain", role="assistant", system_prompt="You are Brain.",
        model_tier="fast", skills=(),
    ))
    synth = _make_synth(db, registry, kuzu=None)

    attached = await synth._attach_to_owner(_cluster("Brain"), "new_skill")

    assert attached is True
