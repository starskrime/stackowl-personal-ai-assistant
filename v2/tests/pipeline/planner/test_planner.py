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
    assert MANDATORY_DISCOVERY <= env.tools
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
