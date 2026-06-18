"""Story 5.1 — Parliament orchestration, fan-out, persistence, timeout, budget."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.parliament.models import (
    ParliamentRound,
    ParliamentSession,
    make_critic_persona,
)
from stackowl.parliament.orchestrator import ParliamentOrchestrator
from stackowl.parliament.session_store import SessionStore
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parliament uses TestModeGuard — disable for unit tests."""
    monkeypatch.setattr(TestModeGuard, "_active", False)


@pytest.fixture()
async def parliament_db(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "parliament.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


class MockOrchestratorBackend(OrchestratorBackend):
    """Deterministic backend returning per-owl canned responses."""

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        delay_s: float = 0.0,
        call_log: list[tuple[str, float]] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.delay_s = delay_s
        self.call_log = call_log if call_log is not None else []

    async def run(self, state: PipelineState) -> PipelineState:
        self.call_log.append((state.owl_name, time.monotonic()))
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        response = self.responses.get(
            state.owl_name, f"[mock response from {state.owl_name}]"
        )
        chunk = ResponseChunk(
            content=response,
            is_final=True,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))


# ---------------------------------------------------------------------------
# ParliamentSession model
# ---------------------------------------------------------------------------


class TestParliamentSession:
    def test_session_creation(self) -> None:
        session = ParliamentSession(topic="should we ship", owl_names=["a", "b"])
        assert session.topic == "should we ship"
        assert session.owl_names == ["a", "b"]
        assert session.status == "running"
        assert session.rounds == []
        assert session.interjections == []
        assert session.completed_at is None
        assert session.session_id  # uuid auto-generated

    def test_add_round_returns_new_session(self) -> None:
        session = ParliamentSession(topic="t", owl_names=["a"])
        round_ = ParliamentRound(round_number=1, responses={"a": "hello"}, truncated={"a": False})
        new = session.add_round(round_)
        assert new is not session
        assert len(new.rounds) == 1
        assert len(session.rounds) == 0
        assert new.rounds[0].round_number == 1

    def test_cumulative_token_estimate(self) -> None:
        session = ParliamentSession(topic="t", owl_names=["a"])
        round_ = ParliamentRound(round_number=1, responses={"a": "x" * 1000}, truncated={"a": False})
        new = session.add_round(round_)
        # 1000 chars / 4 = 250 tokens
        assert new.cumulative_token_estimate() == 250

    def test_fail_sets_status_and_completed_at(self) -> None:
        session = ParliamentSession(topic="t", owl_names=["a"])
        failed = session.fail()
        assert failed.status == "failed"
        assert failed.completed_at is not None

    def test_complete_with_synthesis(self) -> None:
        session = ParliamentSession(topic="t", owl_names=["a"])
        completed = session.complete(synthesis="we agreed")
        assert completed.status == "completed"
        assert completed.synthesis == "we agreed"
        assert completed.completed_at is not None

    def test_add_interjection(self) -> None:
        session = ParliamentSession(topic="t", owl_names=["a"])
        new = session.add_interjection("please reconsider X")
        assert new.interjections == ["please reconsider X"]
        assert session.interjections == []

    def test_critic_persona_has_devil_advocate_role(self) -> None:
        critic = make_critic_persona()
        assert critic.name == "critic"
        assert critic.role == "devil-advocate"
        assert critic.model_tier == "standard"


# ---------------------------------------------------------------------------
# SessionStore persistence
# ---------------------------------------------------------------------------


class TestSessionStore:
    async def test_create_and_fetch(self, parliament_db: DbPool) -> None:
        store = SessionStore(parliament_db)
        original = ParliamentSession(topic="ship it", owl_names=["a", "b"])
        await store.create(original)
        fetched = await store.get_by_id(original.session_id)
        assert fetched is not None
        assert fetched.session_id == original.session_id
        assert fetched.topic == "ship it"
        assert fetched.owl_names == ["a", "b"]
        assert fetched.status == "running"
        assert fetched.rounds == []

    async def test_update_rounds(self, parliament_db: DbPool) -> None:
        store = SessionStore(parliament_db)
        session = ParliamentSession(topic="t", owl_names=["a"])
        await store.create(session)
        round_ = ParliamentRound(round_number=1, responses={"a": "hi"}, truncated={"a": False})
        updated = session.add_round(round_)
        await store.update_rounds(updated)
        fetched = await store.get_by_id(session.session_id)
        assert fetched is not None
        assert len(fetched.rounds) == 1
        assert fetched.rounds[0].responses == {"a": "hi"}

    async def test_list_recent(self, parliament_db: DbPool) -> None:
        store = SessionStore(parliament_db)
        for i in range(7):
            s = ParliamentSession(topic=f"topic {i}", owl_names=["a"])
            await store.create(s)
            # small delay so started_at differs sortable
            await asyncio.sleep(0.005)
        recent = await store.list_recent(limit=5)
        assert len(recent) == 5
        topics = [s.topic for s in recent]
        # newest first
        assert topics[0] == "topic 6"
        assert topics[4] == "topic 2"

    async def test_get_by_id_returns_none_if_missing(self, parliament_db: DbPool) -> None:
        store = SessionStore(parliament_db)
        result = await store.get_by_id("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# ParliamentOrchestrator
# ---------------------------------------------------------------------------


class TestParliamentOrchestrator:
    async def test_run_single_round(self, parliament_db: DbPool) -> None:
        backend = MockOrchestratorBackend({"a": "answer-a", "b": "answer-b"})
        store = SessionStore(parliament_db)
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
        )
        result = await orch.run("topic", ["a", "b"])
        assert result.status == "completed"
        assert len(result.rounds) == 1
        assert result.rounds[0].responses == {"a": "answer-a", "b": "answer-b"}
        assert result.rounds[0].truncated == {"a": False, "b": False}

    async def test_parallel_execution(self, parliament_db: DbPool) -> None:
        """Both owls must be invoked concurrently — call timestamps must overlap."""
        call_log: list[tuple[str, float]] = []
        backend = MockOrchestratorBackend(
            {"a": "x", "b": "y"}, delay_s=0.2, call_log=call_log
        )
        store = SessionStore(parliament_db)
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
        )
        t0 = time.monotonic()
        await orch.run("t", ["a", "b"])
        elapsed = time.monotonic() - t0
        # Two 0.2s calls in parallel should take ~0.2s, not 0.4s.
        assert elapsed < 0.35
        # Both calls should have started within ~50ms of each other.
        starts = sorted(t for _, t in call_log)
        assert starts[1] - starts[0] < 0.05

    async def test_per_owl_timeout(self, parliament_db: DbPool) -> None:
        backend = MockOrchestratorBackend({"slow": "never"}, delay_s=5.0)
        store = SessionStore(parliament_db)
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
            per_owl_timeout_s=0.2,
            session_timeout_s=10.0,
        )
        result = await orch.run("t", ["slow"])
        assert len(result.rounds) == 1
        assert result.rounds[0].truncated["slow"] is True
        assert "timed out" in result.rounds[0].responses["slow"]

    async def test_token_budget_exceeded(self, parliament_db: DbPool) -> None:
        big = "x" * 80_001
        backend = MockOrchestratorBackend({"a": big})
        store = SessionStore(parliament_db)
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
            token_budget=20_000,
        )
        result = await orch.run("t", ["a"])
        # truncated to 500 chars
        assert len(result.rounds[0].responses["a"]) == 500
        assert result.rounds[0].truncated["a"] is True

    async def test_session_timeout(self, parliament_db: DbPool) -> None:
        backend = MockOrchestratorBackend({"a": "x"}, delay_s=10.0)
        store = SessionStore(parliament_db)
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
            per_owl_timeout_s=5.0,
            session_timeout_s=0.3,
        )
        result = await orch.run("t", ["a"])
        assert result.status == "failed"
        assert result.completed_at is not None

    async def test_persists_to_db(self, parliament_db: DbPool) -> None:
        backend = MockOrchestratorBackend({"a": "yes", "b": "no"})
        store = SessionStore(parliament_db)
        orch = ParliamentOrchestrator(
            backend=backend,
            session_store=store,
            max_rounds=1,
        )
        result = await orch.run("topic", ["a", "b"])
        fetched = await store.get_by_id(result.session_id)
        assert fetched is not None
        assert fetched.status == "completed"
        assert len(fetched.rounds) == 1
        assert fetched.rounds[0].responses == {"a": "yes", "b": "no"}

    async def test_test_mode_guard_raises(self, parliament_db: DbPool) -> None:
        from stackowl.config.test_mode import TestModeViolation

        TestModeGuard._active = True  # type: ignore[attr-defined]
        try:
            backend = MockOrchestratorBackend({"a": "x"})
            store = SessionStore(parliament_db)
            orch = ParliamentOrchestrator(
                backend=backend, session_store=store, max_rounds=1
            )
            with pytest.raises(TestModeViolation):
                await orch.run("t", ["a"])
        finally:
            TestModeGuard._active = False  # type: ignore[attr-defined]
