"""D08.1 I5 was true of the class and false of the path production takes.

THE INVARIANT: "Consolidation failures are bounded per turn, so a fragile write
cannot loop the turn to budget exhaustion." `MAX_CONSOLIDATION_FAILURES_PER_TURN`
is 3; past it `_at_capacity` returns a TERMINAL result that drops the retry
instruction, so a model that cannot consolidate stops trying and answers the user.

IT COULD NEVER FIRE. `MemoryTool._curated()` returned a fresh `CuratedMemory()` on
every call, and `_consolidation_failures` lives on that object — so the counter was
destroyed between attempts and could never exceed 1.

MEASURED 2026-09-07 over ten retained days of production logs:

  * `[curated] at_capacity: asking for consolidation` — 42 records;
  * the `attempt` field on every single one of them — **1**. Not one reached 2;
  * `[curated] at_capacity: giving up for this turn` — **0**, ever.

So the bound that reads as protection has never once bounded anything, and the
line that would announce it cannot be emitted.

WHAT THE REFUSALS ACTUALLY DID, correlated by trace rather than subtracted: of the
42 asks, **24** were followed in the same trace by a store/replace/remove, and
**18** by nothing at all. The design reports two outcomes — consolidated, or gave
up after N tries — and the dominant third, *asked once and never came back*, is
reported by neither.

WHY EVERY TEST WAS GREEN, and this is the part worth keeping. The existing fixture
in `test_memory_curated_writes.py` patches `_curated` to
`lambda self: CuratedMemory(root=...)` — a NEW instance per call. It faithfully
reproduces the defect. And I5's own test, `test_repeated_failures_go_terminal...`,
calls `mem.add()` four times on ONE hand-held instance, which is not how the tool
reaches it. Both prove the class; neither touches the path.

So the fixture here holds ONE instance, matching `shared_memory()`, and the wiring
test below asks the real `_curated` whether it returns the same object twice —
because a fixture can be made to pass either way, and the production lifetime is
the whole defect.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import (
    MAX_CONSOLIDATION_FAILURES_PER_TURN,
    USER_TARGET,
    CuratedMemory,
)
from stackowl.tools.knowledge.memory import MemoryTool


@pytest.fixture
def shared(tmp_path, monkeypatch):
    """ONE CuratedMemory behind the tool — what `shared_memory()` gives production."""
    store = CuratedMemory(root=tmp_path / "memory")
    monkeypatch.setattr(MemoryTool, "_curated", lambda self: store)

    class _Bridge:
        async def recall(self, query, limit=5, **kw):  # noqa: ANN001, ANN003, ARG002
            return []

    class _Services:
        memory_bridge = _Bridge()
        db_pool = None
        audit_logger = None
        embedding_registry = None

    monkeypatch.setattr(
        "stackowl.tools.knowledge.memory.get_services", lambda: _Services(),
    )
    return store, MemoryTool()


def _fill(store: CuratedMemory) -> None:
    """Push the profile to capacity so every further write must consolidate.

    EVERY ENTRY IS DISTINCT, and the first version was not. Repeating one string
    hits `add`'s "Entry already present — nothing to do" branch, which returns
    ``ok=True`` WITHOUT growing the file, so `used_chars` never moved and the
    `while` never terminated. The bounded `for` on top is belt-and-braces: a fill
    helper must not be able to hang the suite whatever the store does, because a
    hanging test reads as a broken feature and costs an hour proving it is not.
    """
    budget = store.budget_for(USER_TARGET)
    for i in range(200):
        if store.used_chars(USER_TARGET) >= budget - 60:
            return
        if not store.add(USER_TARGET, f"fact number {i} " + "x" * 40, "permanent").ok:
            return
    raise AssertionError("could not fill the profile in 200 writes")


class TestTheBudgetIsReachableThroughTheTool:
    def test_repeated_refusals_reach_the_cap_and_go_terminal(self, shared) -> None:
        """THE DEFECT, at the tool boundary. Before the fix each call built its own
        CuratedMemory, so `attempt` was 1 forever and this could not be reached."""
        store, _tool = shared
        _fill(store)
        text = "Something far too long to ever fit " + "y" * 400

        results = [
            store.add(USER_TARGET, text, "permanent")
            for _ in range(MAX_CONSOLIDATION_FAILURES_PER_TURN + 1)
        ]

        assert all(r.ok is False for r in results), [r.ok for r in results]
        assert results[-1].done is True, (
            "the cap was never reached, so the model is still being asked to retry "
            "a write that cannot fit — the turn has no bound but its step budget"
        )

    def test_a_successful_write_still_resets_the_budget(self, shared) -> None:
        """The cap counts CONSECUTIVE failures. Sharing the instance must not turn
        it into a lifetime counter that punishes a turn for an earlier one."""
        store, _tool = shared
        store.add(USER_TARGET, "a durable fact", "permanent")
        _fill(store)
        store.add(USER_TARGET, "z" * 900, "permanent")   # one failure
        ok = store.remove(USER_TARGET, "a durable fact")

        assert ok.ok is True
        assert store._consolidation_failures == 0, (
            "a successful write did not clear the streak"
        )


class TestTheLifetimeIsTheFix:
    """A fixture can be written to pass either way. These ask the real thing."""

    def test_the_tool_returns_the_SAME_instance_twice(self) -> None:
        """NAMES THE DEFECT DIRECTLY. Two calls returning two objects is exactly
        how a per-turn counter became per-call."""
        tool = MemoryTool()

        assert tool._curated() is tool._curated(), (
            "MemoryTool builds a new CuratedMemory per call, so the per-turn "
            "consolidation budget is destroyed between attempts and I5 is decoration"
        )

    def test_the_turn_start_reset_has_a_caller(self) -> None:
        """`reset_turn()`'s docstring said "Call at turn start" and NOTHING called
        it — harmless while the object was rebuilt per call, and a cross-turn leak
        the moment it is shared. Both halves of the design or neither."""
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parents[3]
               / "src/stackowl/pipeline/steps/execute.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "reset_turn"
        ]

        assert calls, (
            "nothing calls reset_turn() at turn start, so the shared consolidation "
            "budget now leaks across turns and lanes"
        )


class TestTheRetiredDeduplicatorIsGone:
    """D08.1's "What is removed" list named `fact_reinforcer`. It was still there —
    169 lines, zero imports, no tests, and `scripts/never_called.py` naming it as
    zero-caller code. Deleted 2026-09-07 with its owner-scope allowlist entry."""

    def test_the_module_is_deleted(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]

        assert not (root / "src/stackowl/memory/fact_reinforcer.py").exists()

    def test_it_cannot_be_imported(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            __import__("stackowl.memory.fact_reinforcer")
