"""E2-S3 — PreflightPlanner: proposer ∪ discovery, single-verdict (trustworthy set | None)."""

from __future__ import annotations

from stackowl.authz import BoundsSpec
from stackowl.pipeline.planner.planner import MANDATORY_DISCOVERY, PreflightPlanner


class _Proposer:
    def __init__(self, result):
        self._r = result

    async def propose(self, goal, catalog, *, directives=""):
        # `directives` mirrors the real ToolProposer (ESC-54). A double that
        # omits it does not fail loudly: `plan` catches the TypeError as a
        # provider failure and fails OPEN, so the test sees `env is None` and
        # reads as a planning bug rather than a stale stub.
        self.directives = directives
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
        async def propose(self, goal, catalog, *, directives=""):
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


def test_this_double_still_matches_the_real_proposer() -> None:
    """A double that stops resembling the real thing is failure mode 2 here.

    It bit twice on 2026-08-26 already (the parliament stub, the browser smoke
    args), and then a third time in this very file when ESC-54 added
    ``directives``. THIS ONE HIDES ITSELF: ``plan`` treats any proposer exception
    as a provider failure and fails OPEN, so a stale stub surfaces as
    ``env is None`` — indistinguishable from "the planner declined", which is a
    legitimate outcome. Ask the real class instead of reading the traceback.
    """
    import inspect

    from stackowl.pipeline.planner.proposer import ToolProposer

    real = set(inspect.signature(ToolProposer.propose).parameters) - {"self"}
    stub = set(inspect.signature(_Proposer.propose).parameters) - {"self"}
    assert stub == real, (
        f"the proposer double has drifted: stub {sorted(stub)}, "
        f"real {sorted(real)}"
    )
