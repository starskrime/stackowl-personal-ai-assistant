"""F075 — concurrent Parliament sessions must not clobber each other.

The active-session state was a process-wide ``ClassVar`` slot: two concurrent
``run()`` calls on the one DI singleton overwrote each other (B clobbers A; A's
``finally`` nulled B; ``inject_interjection`` misrouted). The fix makes the active
session an INSTANCE dict keyed by ``session_id`` with single-or-refuse routing.

These tests race two real sessions via ``asyncio.gather`` (a barrier backend parks
both inside ``_run_session`` so they truly overlap) and assert each session sees ONLY
its own interjection — a serial test passes the buggy code, only the overlap exposes
the clobber.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.parliament.orchestrator import ParliamentOrchestrator
from stackowl.parliament.session_store import SessionStore
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
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


class _GatedBackend(OrchestratorBackend):
    """Parks every round at a shared gate so concurrent sessions overlap, then
    optionally times out one owl name forever (to drive the timeout-isolation test)."""

    def __init__(self, gate: asyncio.Event, *, hang_owl: str | None = None) -> None:
        self._gate = gate
        self._hang_owl = hang_owl

    async def run(self, state: PipelineState) -> PipelineState:
        if self._hang_owl is not None and state.owl_name == self._hang_owl:
            await asyncio.sleep(3600)  # never returns → session timeout fires
        await self._gate.wait()
        chunk = ResponseChunk(
            content="done", is_final=True, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))


def _orch(db: DbPool, backend: OrchestratorBackend, **kw: float) -> ParliamentOrchestrator:
    return ParliamentOrchestrator(
        backend=backend, session_store=SessionStore(db), max_rounds=1,
        per_owl_timeout_s=kw.get("per_owl_timeout_s", 2.0),
        session_timeout_s=kw.get("session_timeout_s", 5.0),
    )


async def test_two_sessions_no_interjection_clobber(parliament_db: DbPool) -> None:
    gate = asyncio.Event()
    orch = _orch(parliament_db, _GatedBackend(gate))

    task_a = asyncio.create_task(orch.run("topic-A", ["a"], session_id="SESS-A"))
    task_b = asyncio.create_task(orch.run("topic-B", ["b"], session_id="SESS-B"))
    await asyncio.sleep(0.05)  # both parked inside _run_session, both "active"

    assert await orch.inject_interjection("for-A", session_id="SESS-A") is True
    assert await orch.inject_interjection("for-B", session_id="SESS-B") is True

    gate.set()
    final_a, final_b = await asyncio.gather(task_a, task_b)

    # Each session carries ONLY its own interjection — no cross-contamination.
    assert final_a.interjections == ["for-A"], final_a.interjections
    assert final_b.interjections == ["for-B"], final_b.interjections


async def test_timeout_fails_only_its_own_session(parliament_db: DbPool) -> None:
    gate = asyncio.Event()
    # Session B's owl hangs forever → B times out; A completes normally.
    backend = _GatedBackend(gate, hang_owl="b")
    orch = _orch(parliament_db, backend, session_timeout_s=0.3)

    task_a = asyncio.create_task(orch.run("topic-A", ["a"], session_id="SESS-A"))
    task_b = asyncio.create_task(orch.run("topic-B", ["b"], session_id="SESS-B"))
    await asyncio.sleep(0.05)
    gate.set()  # frees A's round; B is stuck in the 3600s hang → times out

    final_a, final_b = await asyncio.gather(task_a, task_b)
    assert final_a.status != "failed"  # A unaffected by B's timeout
    assert final_b.status == "failed"  # only B failed


async def test_ambiguous_unscoped_push_refuses_loudly(parliament_db: DbPool) -> None:
    gate = asyncio.Event()
    orch = _orch(parliament_db, _GatedBackend(gate))

    task_a = asyncio.create_task(orch.run("topic-A", ["a"], session_id="SESS-A"))
    task_b = asyncio.create_task(orch.run("topic-B", ["b"], session_id="SESS-B"))
    await asyncio.sleep(0.05)  # TWO live sessions

    # Unscoped push with two live debates → refuse, never silently pick one.
    assert await orch.inject_interjection("ambiguous") is False

    gate.set()
    final_a, final_b = await asyncio.gather(task_a, task_b)
    assert final_a.interjections == []  # neither mutated
    assert final_b.interjections == []


async def test_unscoped_push_routes_to_sole_session(parliament_db: DbPool) -> None:
    gate = asyncio.Event()
    orch = _orch(parliament_db, _GatedBackend(gate))

    task_a = asyncio.create_task(orch.run("topic-A", ["a"], session_id="SESS-A"))
    await asyncio.sleep(0.05)  # exactly ONE live session

    assert await orch.inject_interjection("only-one") is True  # the natural target

    gate.set()
    final_a = await task_a
    assert final_a.interjections == ["only-one"]
