"""ADR-5 — trustworthy/verified-gated learning.

Two invariants this iteration:

* MOVE 1 — the learning loop only ever mines MEASURED success. A tool whose effect was
  not verified (persisted as ``failure_class="unachieved_effect"`` — the ADR-1/B4b proxy
  for ``verified=False``) is never mined as a win.
* MOVE 2 (F-50) — reflection recall on the live path becomes SEMANTIC when
  ``settings.trustworthy_learning`` is ON (matching the current intent), and is
  recency-only (byte-identical) when OFF.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.db.pool import DbPool
from stackowl.learning.tool_heuristic_store import ToolHeuristicStore
from stackowl.learning.tool_outcome_miner import ToolOutcomeMiner
from stackowl.memory.outcome_store import TaskOutcome

# --------------------------------------------------- MOVE 1: mine measured success only


def _outcome(trace: str, tool: str, failure_class: str | None) -> TaskOutcome:
    return TaskOutcome(
        outcome_id=0,
        trace_id=trace,
        session_id="s",
        owl_name="o",
        channel="cli",
        success=failure_class is None,
        latency_ms=1.0,
        tool_call_count=1,
        failure_class=failure_class,
        quality_score=0.9,
        step_durations={},
        input_text="x",
        response_text="y",
        captured_at=0.0,
        scored_at=1.0,
        tool_sequence=(tool,),
    )


class _FakeOutcomes:
    def __init__(self, outcomes: list[TaskOutcome]) -> None:
        self._outcomes = outcomes

    async def list_scored_for_owl_global(self, *, since_epoch: float) -> list[TaskOutcome]:
        return self._outcomes


async def test_verified_false_outcome_is_never_mined_as_a_win(tmp_db: DbPool) -> None:
    """The ADR-5 MOVE 1 invariant: an outcome whose effect was NOT verified
    (failure_class='unachieved_effect', the ADR-1/B4b persisted proxy for verified=False)
    is excluded from mining — only MEASURED wins shape behavior."""
    heur = ToolHeuristicStore(tmp_db)
    outcomes = _FakeOutcomes(
        [
            _outcome("t1", "good_tool", None),  # measured win
            _outcome("t2", "bad_tool", "unachieved_effect"),  # verified=False proxy
        ]
    )
    miner = ToolOutcomeMiner(outcomes, heur, lessons_index=None, min_evidence=1)  # type: ignore[arg-type]

    await miner.mine()

    assert await heur.find_for_tool("good_tool", min_evidence=1)  # mined
    assert not await heur.find_for_tool("bad_tool", min_evidence=1)  # excluded at source


# --------------------------------------------------- MOVE 2 (F-50): semantic recall


class _SpyReflectionStore:
    last_method: str | None = None

    def __init__(self, db: object) -> None:  # noqa: D107
        pass

    async def recent_for_owl(self, owl_name: str, limit: int = 5) -> list:
        type(self).last_method = "recent"
        return []

    async def semantic_for_owl(
        self, owl_name: str, query: str, embeddings: object, *, limit: int = 5
    ) -> list:
        type(self).last_method = "semantic"
        return []


def _wire(monkeypatch: pytest.MonkeyPatch, *, flag: bool, embeddings: object) -> None:
    import stackowl.config.settings as settings_mod
    import stackowl.memory.reflection_store as reflection_mod
    from stackowl.pipeline.steps import classify as classify_mod

    _SpyReflectionStore.last_method = None
    monkeypatch.setattr(reflection_mod, "ReflectionStore", _SpyReflectionStore)
    monkeypatch.setattr(
        settings_mod, "Settings", lambda: SimpleNamespace(trustworthy_learning=flag)
    )
    fake_services = SimpleNamespace(db_pool=object(), embedding_registry=embeddings)
    monkeypatch.setattr(classify_mod, "get_services", lambda: fake_services)


async def test_recall_is_recency_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from stackowl.pipeline.steps.classify import _gather_recent_reflections

    _wire(monkeypatch, flag=False, embeddings=object())
    await _gather_recent_reflections("owl", query="how do I deploy?", limit=3)
    assert _SpyReflectionStore.last_method == "recent"


async def test_recall_is_semantic_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from stackowl.pipeline.steps.classify import _gather_recent_reflections

    _wire(monkeypatch, flag=True, embeddings=object())
    await _gather_recent_reflections("owl", query="how do I deploy?", limit=3)
    assert _SpyReflectionStore.last_method == "semantic"


async def test_recall_falls_back_to_recency_when_no_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON but no embedding registry ⇒ recency (semantic needs embeddings)."""
    from stackowl.pipeline.steps.classify import _gather_recent_reflections

    _wire(monkeypatch, flag=True, embeddings=None)
    await _gather_recent_reflections("owl", query="how do I deploy?", limit=3)
    assert _SpyReflectionStore.last_method == "recent"


async def test_recall_is_recency_when_query_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON, embeddings present, but empty query ⇒ recency (nothing to match)."""
    from stackowl.pipeline.steps.classify import _gather_recent_reflections

    _wire(monkeypatch, flag=True, embeddings=object())
    await _gather_recent_reflections("owl", query="   ", limit=3)
    assert _SpyReflectionStore.last_method == "recent"
