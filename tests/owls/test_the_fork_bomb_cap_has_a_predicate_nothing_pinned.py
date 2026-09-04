"""The depth increment is the single predicate both fork-bomb layers read.

MEASURED 2026-09-04 BY MUTATION. Deleting the `+ 1` from
`a2a_delegation._run_specialist`'s child state — so a delegated child runs at its
PARENT's depth — leaves **481 tests passing**: tests/tools/test_sec3_child_self_defense,
tests/tools/agents and tests/owls, all green with the fork-bomb cap neutered.

WHY THAT IS SERIOUS. The platform has two layers against a child spawning children:

  presentation  execute.py removes the spawn/delegate tools when delegation_depth > 0
  dispatch      execute.py refuses them BY NAME when delegation_depth > 0

That reads as defence in depth, and it is not: **both layers gate on the same two
things**, `delegation_depth > 0` and `_CHILD_EXCLUDED_TOOLS`. Layers that share a
predicate are one layer. If the depth stops incrementing, both fail together and
silently — the presentation exclusion logs at DEBUG, so in production there is no
line at all to notice its absence.

WHY THIS TEST IS STRUCTURAL RATHER THAN BEHAVIOURAL, stated rather than hidden:
the increment is one keyword inside a long `evolve(...)` call in an async method
whose behavioural exercise needs a full sub-pipeline. A source pin is weaker in
general — an earlier guard in this programme asserted a string appeared *somewhere*
and was satisfied by one call site while two others drifted. It is strong HERE
because there is exactly ONE increment site in the tree, and this test asserts the
expression at that site rather than its presence anywhere.
"""

from __future__ import annotations

import ast
import inspect

from stackowl.owls import a2a_delegation


def _child_depth_expression() -> str | None:
    """The expression assigned to `delegation_depth=` in the child's evolve call."""
    tree = ast.parse(inspect.getsource(a2a_delegation))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "delegation_depth":
                return ast.unparse(kw.value)
    return None


def test_a_delegated_child_runs_one_level_deeper_than_its_parent() -> None:
    """The mutation that 481 tests missed."""
    expr = _child_depth_expression()
    assert expr is not None, "no delegation_depth is set on the child state at all"
    assert expr == "parent_state.delegation_depth + 1", (
        "the child's depth is not parent + 1, so BOTH fork-bomb layers — the "
        f"presentation exclusion and the dispatch refusal — stop firing: {expr!r}"
    )


def test_there_is_exactly_ONE_increment_site() -> None:
    """What makes a source pin sound here rather than merely convenient.

    Two sites would mean this test could pass while the other drifted, which is
    exactly how an earlier guard in this programme failed.
    """
    tree = ast.parse(inspect.getsource(a2a_delegation))
    sites = [
        kw
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "delegation_depth"
    ]
    assert len(sites) == 1, f"expected one increment site, found {len(sites)}"


def test_both_layers_still_read_the_same_predicate() -> None:
    """Pins the shared-predicate FACT, so a future reader is not misled.

    If someone later makes the two layers independent, this test fails and should
    be updated — that is a real improvement, and it should be a deliberate one
    rather than something discovered by mutation a second time.
    """
    from stackowl.pipeline.steps import execute

    src = inspect.getsource(execute)
    assert src.count("state.delegation_depth > 0") >= 2, (
        "the presentation and dispatch layers no longer both gate on depth"
    )
