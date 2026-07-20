"""ParliamentOrchestrator production wiring — static regression.

Confirmed defect: startup/orchestrator.py's single production construction
site built ParliamentOrchestrator with only backend/session_store/
delegation_governor. No synthesizer means every real /parliament session
completed with synthesis=None ("no synthesis produced"); no
convergence_detector means ConvergenceDetector() ran with no embedding
registry and permanently returned False (no early-exit ever fired); no
memory_bridge means the pellet generator that KnowledgePelletGenerator
auto-wires from it was never constructed, so pellets never staged into
memory. All three pieces of logic already existed and were already tested in
isolation (tests/parliament/*) — only the production call site was missing
them.

orchestrator.py's run() is a large integration method with heavy I/O side
effects, not practically unit-testable end to end, so this pins the fix at
the source level (same static-assertion style as the existing B8
forbidden-import test in tests/test_story_3_6.py) rather than exercising a
full boot.
"""

from __future__ import annotations

import ast
import inspect

import stackowl.startup.orchestrator as orch_module


def _find_parliament_orchestrator_call() -> ast.Call:
    source = inspect.getsource(orch_module)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ParliamentOrchestrator"
        ):
            return node
    raise AssertionError("no ParliamentOrchestrator(...) construction found in startup/orchestrator.py")


def test_parliament_orchestrator_wired_with_synthesizer_convergence_and_memory() -> None:
    call = _find_parliament_orchestrator_call()
    kwarg_names = {kw.arg for kw in call.keywords}
    assert "synthesizer" in kwarg_names, "synthesis is dead in production without this"
    assert "convergence_detector" in kwarg_names, "convergence always returns False without this"
    assert "memory_bridge" in kwarg_names, "knowledge pellets never stage into memory without this"
