"""Cost is billed to the owl that RAN the turn, not the owl whose lane it arrived on.

WHY THIS EXISTS, and it was found by re-verifying a six-week-old design document.

D01.1's invariant is `COUNT(DISTINCT prompt_hash) per conversation == 1` — one frozen
system prompt for the life of a conversation. Its verification query had been unrunnable
since migration 0121 renamed `session_id` to `conversation_id`, so nobody had checked it
in three weeks. Run corrected on 2026-09-07 it reported 1,775 conversations and 61
violations, 60 of them August legacy and ONE from the previous evening.

That one was real and it was not the prompt's fault. Conversation
`20260906_174450_49c9d55a` carried two hashes: 13,795 chars for 57 calls, then 12,241
for 6. No restart (one process since 21:55Z) and no session boundary — the sweep in that
window logged `count: 0` and the next resolve took the `existing` branch. What actually
happened is in the triage line: `[pipeline] triage: routed {'owl': 'syshealth'}` on a
lane named `owl:secretary:telegram:dm:72055773`. A DIFFERENT OWL answered, and a
different owl legitimately has a different system prompt.

`providers/base.py` already knows this. The comment above its `owl_name` read says the
column exists because "grouping by conversation_id alone counts a correct design as a
violation" — per-owl grouping was built precisely for this case.

IT COULD NEVER WORK, because the value it grouped on was wrong. `TraceContext.start()` is
called in `bind_turn_context` with `owl_name=state.owl_name` BEFORE any step runs. Triage
then re-routes by returning `state.evolve(owl_name=...)` — at four separate sites — and
nothing re-binds the trace copy. `providers/base.py:390` reads `ctx.get("owl_name")` and
gets the lane's owl forever. All 63 rows of that conversation say `secretary`, including
the 6 that were syshealth's work.

MEASURED over the retained logs: of 363 `triage: routed` records whose lane could be
parsed, 162 — FORTY-FIVE PERCENT — routed to an owl other than the lane's. secretary ->
jobmarket 110, -> mailbutler 27, -> headhunter 11, verifier -> secretary 7, -> scout 6,
-> syshealth 1. Every one of those turns billed the wrong owl.

THE FIX IS AT THE ONE PLACE THE STATE ADVANCES. Patching triage would mean patching four
return sites and hoping the next routing path remembers; the state is authoritative after
every step, so the trace copy is synced there. And there are TWO step loops —
`asyncio_backend` and `langgraph_backend` — so the sync lives in `shared.py` and both
call it. Fixing one backend would have been the same two-copies-of-one-fact defect this
file exists to close.
"""

from __future__ import annotations

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.backends.shared import sync_turn_owl


class TestTheTraceOwlFollowsTheState:
    def test_it_updates_when_a_step_reroutes(self) -> None:
        """The defect, directly: bind the lane's owl, route elsewhere, read it back."""
        token = TraceContext.start("owl:secretary:telegram:dm:1", owl_name="secretary")
        try:
            assert TraceContext.get()["owl_name"] == "secretary"

            sync_turn_owl("syshealth")

            assert TraceContext.get()["owl_name"] == "syshealth", (
                "the trace context still names the lane's owl after routing, so every "
                "cost row for this turn bills the wrong owl"
            )
        finally:
            TraceContext.reset(token)

    def test_it_is_a_no_op_when_the_owl_did_not_change(self) -> None:
        token = TraceContext.start("owl:secretary:telegram:dm:1", owl_name="secretary")
        try:
            sync_turn_owl("secretary")

            assert TraceContext.get()["owl_name"] == "secretary"
        finally:
            TraceContext.reset(token)

    def test_an_empty_owl_never_erases_a_real_one(self) -> None:
        """A step that carries no owl must not blank the attribution. Losing the
        attribution is never worth losing the row — base.py's own words."""
        token = TraceContext.start("owl:secretary:telegram:dm:1", owl_name="secretary")
        try:
            sync_turn_owl("")
            sync_turn_owl(None)

            assert TraceContext.get()["owl_name"] == "secretary"
        finally:
            TraceContext.reset(token)

    def test_the_start_token_still_restores_the_previous_value(self) -> None:
        """Syncing mid-turn must not break teardown. `unbind_turn_context` resets with
        the token `start()` returned, and a plain `set()` in between leaves that token
        valid — this pins that, because getting it wrong would leak an owl across turns."""
        outer = TraceContext.start("owl:a:cli:dm:1", owl_name="alpha")
        try:
            inner = TraceContext.start("owl:b:cli:dm:1", owl_name="beta")
            sync_turn_owl("gamma")
            assert TraceContext.get()["owl_name"] == "gamma"
            TraceContext.reset(inner)

            assert TraceContext.get()["owl_name"] == "alpha", (
                "the mid-turn sync broke the reset token, so an owl leaked across turns"
            )
        finally:
            TraceContext.reset(outer)


class TestBothStepLoopsSyncIt:
    """THE TWO-COPIES CHECK. There are two backends running the same step list. A fix
    applied to one is the defect this item is about, wearing the fixer's clothes."""

    @pytest.mark.tripwire
    def test_every_step_loop_syncs_the_owl(self) -> None:
        """ASKS THE AST FOR A CALL, and the first version of this test did not.

        It checked `"sync_turn_owl" in text`. Deleting the call line from either
        backend left the IMPORT behind, so the string was still present and both
        mutants SURVIVED — a guard against the two-copies defect that was itself
        satisfied by a copy. Found by mutating, not by reading.
        """
        import ast
        from pathlib import Path

        backends = Path(__file__).resolve().parents[2] / "src/stackowl/pipeline/backends"
        loops = {}
        for path in (backends / "asyncio_backend.py", backends / "langgraph_backend.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            called = {
                n.func.id
                for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            loops[path.name] = ("step_fn" in called, "sync_turn_owl" in called)

        missing = {
            name: "awaits step_fn but never CALLS sync_turn_owl (an import is not a call)"
            for name, (awaits, syncs) in loops.items()
            if awaits and not syncs
        }

        assert not missing, (
            f"a step loop advances state without syncing the trace owl: {missing}"
        )

    def test_the_check_sees_both_backends(self) -> None:
        """VACUITY CONTROL. If neither file were found to await a step, the assertion
        above would pass over an empty set."""
        import ast
        from pathlib import Path

        backends = Path(__file__).resolve().parents[2] / "src/stackowl/pipeline/backends"
        n = 0
        for path in (backends / "asyncio_backend.py", backends / "langgraph_backend.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(
                isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                and x.func.id == "step_fn"
                for x in ast.walk(tree)
            ):
                n += 1

        assert n == 2, f"expected two step loops awaiting step_fn, found {n}"
