"""D18.6 — a test that asserts how MANY things exist breaks when things are added.

The reference platform names this anti-pattern: never assert on data expected to
change — model catalogs, config version literals, enumeration counts — and test the
INVARIANT instead ("every model in the catalog has a context-length entry").

MEASURED 2026-09-05, AND THE RULE AS STATED IS TOO BLUNT. An AST sweep of 1,611 test
files found **937** assertions of the shape `len(X) == <int>`. Almost all are correct:
`assert len(rows) == 1` after inserting one row is a contract about the operation, not
a snapshot of anything. Banning the shape would have meant deleting 900+ good tests.

Narrowing to counts of collections the codebase GROWS left **five real instances**,
all now converted, and the git history shows what they cost:

    tests/setup/test_provider_catalog.py   len(entries) == 49, twice, and == 50
                                           bumped 15 -> 17 -> 49 over its life; one
                                           commit added 32 providers and had to edit
                                           this test to do it
    tests/tools/test_discovery.py          len(names) == 77 on the tool registry —
                                           written to prove parity with the
                                           hand-written list discovery replaced, a
                                           migration check valid once
    tests/scheduler/test_graph_reconciliation.py
                                           len(ids) == 7  # one per TRAIT_NAMES entry
                                           — the comment already stated the
                                           relationship the assertion refused to

**AND THE CARVE-OUT MATTERS MORE THAN THE RULE.** Two count assertions in this repo
are deliberate and must stay:

    tests/tenancy/test_the_closed_axis_set_stays_closed_in_both_places.py  == 6
    tests/plugins/test_the_core_tree_does_not_grow_a_vendor_connector.py   == 4

Both guard sets that are CLOSED BY POLICY, and both say so in their own message — "a
fifth means the rule was abandoned rather than amended". For those, breaking on growth
IS the function. A ban that cannot tell them from the catalog would force someone to
weaken a multi-tenancy guard to satisfy a lint rule, and the costs are asymmetric: the
catalog error costs a one-line edit, while deleting a closed-set assertion silently
un-guards an authz enumeration and nothing would ever say so.

So the rule this file enforces is narrower than "no count assertions":

    A count literal is legitimate when the TEST produced the number, or when the
    number is the subject of a stated closure decision. It is the anti-pattern when
    it passively echoes a fact owned somewhere else.

Mechanically: an equality against a literal > 3, on a collection whose value traces to
a load/discovery/registry call, where the number appears nowhere else in the test.

WHY A GUARD AT ALL, when the reference enforces this with prose? Because measuring
their tree shows what prose achieves. They state the rule in their contributor guide,
have no lint rule, no CI job and no test for it — and their own suite violates it
verbatim, including a file that cites the rule by name on one line while breaking it
150 lines earlier. A rule with no enforcer buys docstring citations.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

_TESTS = pathlib.Path(__file__).resolve().parents[1] / "tests"

#: A collection whose contents are owned elsewhere — loaded, discovered, registered.
_PROVENANCE = re.compile(
    r"\.load\(|\.all\(|discover|list_|catalog|registry|\.entries\(|with_defaults", re.I
)

#: Flagged, inspected, and legitimate. Each entry says WHY, because an allowlist whose
#: entries have no reason becomes a place to hide things.
_JUSTIFIED: dict[tuple[str, int], str] = {
    ("skills/test_catalogue_order_is_by_value.py", 4): (
        "the test builds exactly four (name, runs, state) tuples inline and asserts "
        "four DISTINCT sort keys come back — the number is the test's own input, not "
        "a fact owned elsewhere. The heuristic cannot see it because the literal 4 "
        "never appears in the tuples themselves."
    ),
}


def _enclosing_function(tree: ast.AST, node: ast.AST) -> ast.AST | None:
    best = None
    for candidate in ast.walk(tree):
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(candidate, "end_lineno", candidate.lineno)
            if candidate.lineno <= node.lineno <= end:
                if best is None or candidate.lineno > best.lineno:
                    best = candidate
    return best


def _snapshot_counts() -> list[tuple[str, int, int, str]]:
    """(relative path, line, literal, what is being counted)."""
    found: list[tuple[str, int, int, str]] = []
    for path in sorted(_TESTS.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            test = node.test
            if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
                continue
            if not isinstance(test.ops[0], ast.Eq):
                continue
            left, right = test.left, test.comparators[0]
            if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Name)):
                continue
            if left.func.id != "len" or not left.args:
                continue
            if not isinstance(right, ast.Constant) or not isinstance(right.value, int):
                continue
            if isinstance(right.value, bool) or right.value <= 3:
                continue

            function = _enclosing_function(tree, node)
            if function is None:
                continue

            # Did the TEST produce this number? If the literal appears elsewhere in the
            # same function it is almost always the test's own input.
            body = ast.get_source_segment(src, function) or ""
            if len(re.findall(rf"(?<![\w.]){right.value}(?![\w])", body)) > 1:
                continue

            # THE PROVENANCE IS IN THE ASSIGNMENT, NOT THE ASSERT. The first version of
            # this walker read only `len(x)` and found NOTHING — including the two
            # instances it was written from. A control caught it; a green run would not
            # have.
            arg = left.args[0]
            texts = [ast.get_source_segment(src, arg) or ""]
            if isinstance(arg, ast.Name):
                for stmt in ast.walk(function):
                    targets: list[ast.expr] = []
                    if isinstance(stmt, ast.Assign):
                        targets = list(stmt.targets)
                    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                        targets = [stmt.target]
                    if any(isinstance(t, ast.Name) and t.id == arg.id for t in targets):
                        value = getattr(stmt, "value", None)
                        if value is not None:
                            texts.append(ast.get_source_segment(src, value) or "")
            if not any(_PROVENANCE.search(t) for t in texts):
                continue

            found.append(
                (str(path.relative_to(_TESTS)), node.lineno, right.value,
                 " | ".join(t for t in texts if t)[:90])
            )
    return found


@pytest.mark.tripwire
def test_no_test_asserts_the_size_of_a_collection_it_does_not_own() -> None:
    unjustified = [
        (f, line, value, what)
        for f, line, value, what in _snapshot_counts()
        if (f, value) not in _JUSTIFIED
    ]
    assert not unjustified, (
        "count assertion on a collection this test does not own:\n"
        + "\n".join(f"  {f}:{line}  == {value}   ({what})" for f, line, value, what in unjustified)
        + "\n\nD18.6: assert the RELATIONSHIP, not the number. Derive it from the same "
        "source the code reads (`len(_bundled_names())`, `len(TRAIT_NAMES)`), or compare "
        "the two populations. If the set is CLOSED BY POLICY and breaking on growth is "
        "the point, say so in the assertion message and add it here with that reason."
    )


def test_the_allowlist_still_describes_something_real() -> None:
    """A list that outlives its subjects stops describing anything."""
    live = {(f, value) for f, _, value, _ in _snapshot_counts()}
    stale = sorted(set(_JUSTIFIED) - live)
    assert not stale, f"allowlisted but no longer present: {stale}. Remove them."


def test_the_detector_can_actually_see_the_shape_it_bans() -> None:
    """THE CONTROL, and it is not optional.

    The first version of this walker returned ZERO on a tree that contained five
    instances, because it looked for provenance in the assert instead of the
    assignment. Zero flags is exactly what a working guard looks like once the tree is
    clean, so without a positive control there is nothing to distinguish "clean" from
    "blind" — and this repo has already shipped a guard that pointed at nothing.
    """
    source = '''
def test_thing():
    entries = SomeCatalog.load()
    assert len(entries) == 49
'''
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assert))
    function = _enclosing_function(tree, node)
    assert function is not None

    left = node.test.left           # type: ignore[attr-defined]
    arg = left.args[0]
    texts = [ast.get_source_segment(source, arg) or ""]
    for stmt in ast.walk(function):
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == arg.id for t in stmt.targets
        ):
            texts.append(ast.get_source_segment(source, stmt.value) or "")

    assert any(_PROVENANCE.search(t) for t in texts), (
        "the detector cannot see a catalog count — it would report a clean tree "
        "whatever the tree contained"
    )
