"""E2-S3 — PreflightPlanner: proposer ∪ discovery, single-verdict (trustworthy set | None)."""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.pipeline.planner.planner import MANDATORY_DISCOVERY, PreflightPlanner


class _Proposer:
    def __init__(self, result):
        self._r = result

    async def propose(self, goal, catalog):
        return self._r


CATALOG = [("note_search", "d"), ("summarize_text", "d"), ("tool_search", "d"), ("tool_describe", "d")]
OWL = BoundsSpec(tools=frozenset({"note_search", "summarize_text", "shell", "tool_search", "tool_describe"}))


async def test_unions_mandatory_discovery() -> None:
    env = await PreflightPlanner(_Proposer(frozenset({"note_search"}))).plan("g", OWL, CATALOG)
    assert env is not None
    assert env.tools >= MANDATORY_DISCOVERY
    assert "note_search" in env.tools


async def test_empty_proposer_returns_none() -> None:
    # discovery-only would hide the whole real toolset (self-DoS) → decline.
    assert await PreflightPlanner(_Proposer(frozenset())).plan("g", OWL, CATALOG) is None


async def test_tools_only_envelope_passes_honesty_guard() -> None:
    env = await PreflightPlanner(_Proposer(frozenset({"note_search"}))).plan("g", OWL, CATALOG)
    assert env is not None and env.fs_read_roots is None and env.network is None


async def test_proposer_raising_returns_none() -> None:
    class _Boom:
        async def propose(self, goal, catalog):
            raise RuntimeError("x")
    assert await PreflightPlanner(_Boom()).plan("g", OWL, CATALOG) is None


async def test_honesty_guard_failure_returns_none(monkeypatch) -> None:  # noqa: ANN001
    # Defensive: if a future planner produced a non-tools-axis narrowing, the
    # honesty guard raises DomainError and the planner fails open to None.
    from stackowl.exceptions import DomainError

    def _raise(owl, task):  # noqa: ANN001, ANN202
        raise DomainError("non-tools axis narrowed")

    monkeypatch.setattr(
        "stackowl.pipeline.planner.planner.assert_task_narrowing_enforceable", _raise
    )
    env = await PreflightPlanner(_Proposer(frozenset({"note_search"}))).plan("g", OWL, CATALOG)
    assert env is None
