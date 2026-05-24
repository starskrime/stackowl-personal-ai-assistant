"""Story 6.7 — end-to-end memory pipeline integration test.

Exercises the full Epic 6 pipeline using a real SqliteMemoryBridge backed by
an on-disk temp DB:

1. Extract two facts via FactExtractor (stub LLM)
2. Stage them in SqliteMemoryBridge
3. Reinforce both via direct SQL
4. Promote eligible via FactPromoter
5. Recall via bridge.recall()
6. Stage a Parliament pellet via KnowledgePelletGenerator
7. Prune (no-op on fresh data) via MemoryPruner
8. list_staged(status='committed') returns both promoted facts
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.fact_extractor import FactExtractor
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.pruner import MemoryPruner
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.parliament.models import ParliamentSession
from stackowl.parliament.pellet_generator import KnowledgePelletGenerator
from stackowl.parliament.synthesis_models import (
    DisagreementPoint,
    SynthesisResult,
)
from stackowl.providers.base import CompletionResult, Message


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _no_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable TestModeGuard for the full E2E run."""
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[DbPool, None]:
    """Per-test fresh DbPool with every migration applied."""
    db_path = tmp_path / "story67_e2e.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


class _StubProvider:
    """ModelProvider stub yielding a fixed 2-fact JSON response."""

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> str:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: Any
    ) -> CompletionResult:
        self.calls.append(messages)
        body = (
            '[{"content": "User uses Python 3.14", "confidence": 0.9},'
            ' {"content": "User prefers TypeScript on frontend", "confidence": 0.85}]'
        )
        return CompletionResult(
            content=body,
            input_tokens=10,
            output_tokens=20,
            model="stub-m",
            provider_name="stub",
            duration_ms=1.0,
        )

    def stream(self, *_a: Any, **_kw: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


async def _force_reinforce(db: DbPool, fact_id: str, count: int) -> None:
    """Bump reinforcement_count via direct SQL — bypass the cosine path."""
    await db.execute(
        "UPDATE staged_facts SET reinforcement_count = ? WHERE fact_id = ?",
        (count, fact_id),
    )


async def test_full_memory_pipeline_end_to_end(db: DbPool) -> None:
    """E2E pipeline: extract → stage → reinforce → promote → recall → pellet → prune."""
    # 1. Set up bridge and extractor
    bridge = SqliteMemoryBridge(db)
    provider = _StubProvider()
    extractor = FactExtractor(provider=provider)  # type: ignore[arg-type]

    # 2. Run extraction
    conversation = [
        Message(role="user", content="I write Python 3.14 daily."),
        Message(role="assistant", content="Got it — also frontend?"),
        Message(role="user", content="Yes, TypeScript on the frontend."),
    ]
    drafts = await extractor.extract(conversation, "conv_001")
    assert len(drafts) == 2

    # 3. Stage each fact via bridge.stage()
    for fact in drafts:
        await bridge.stage(fact)
    staged_rows = await db.fetch_all(
        "SELECT fact_id FROM staged_facts WHERE status = 'staged'"
    )
    assert len(staged_rows) == 2

    # 4. Force reinforcement directly so promotion gate is satisfied
    for fact in drafts:
        await _force_reinforce(db, fact.fact_id, count=3)

    # 5. Promote eligible facts
    promoter = FactPromoter(db=db, confidence_threshold=0.8, reinforcement_required=3)
    promoted = await promoter.promote_eligible()
    assert promoted == 2

    # 6. Recall both via bridge.recall (FTS5 fallback path)
    hits = await bridge.recall("Python", limit=10)
    assert any("Python" in r.content for r in hits)

    # 7. Stage a Parliament pellet via KnowledgePelletGenerator + real bridge
    pellet_gen = KnowledgePelletGenerator(memory_bridge=bridge)
    session = ParliamentSession(
        topic="should we adopt FastAPI?",
        owl_names=["secretary", "critic"],
        session_id="parl-e2e-1",
    )
    synthesis = SynthesisResult(
        consensus="FastAPI is a strong fit for our async I/O profile.",
        disagreements=[
            DisagreementPoint(
                claim="async ORM choice",
                positions={"secretary": "SQLAlchemy 2.0", "critic": "Tortoise"},
            )
        ],
        recommendation="adopt",
        confidence=0.78,
        synthesis_text="full",
    )
    await pellet_gen.from_parliament(session, synthesis)
    parl_rows = await db.fetch_all(
        "SELECT content, source_type, source_ref FROM staged_facts "
        "WHERE source_type = 'parliament'"
    )
    assert len(parl_rows) == 2
    sources = {r["source_ref"] for r in parl_rows}
    assert sources == {"parliament:parl-e2e-1"}

    # 8. Pruner has nothing prunable on fresh data → 0 pruned
    pruner = MemoryPruner(db=db, prune_after_days=90, confidence_threshold=0.4)
    report = await pruner.prune()
    assert report.pruned_count == 0
    assert report.kept_count >= 2

    # 9. list_staged(committed) returns the 2 promoted facts
    committed = await bridge.list_staged(status="committed")
    assert len(committed) == 2
    contents = {f.content for f in committed}
    assert any("Python" in c for c in contents)
    assert any("TypeScript" in c for c in contents)

    # 10. Provider was called exactly once during extraction
    assert len(provider.calls) == 1
