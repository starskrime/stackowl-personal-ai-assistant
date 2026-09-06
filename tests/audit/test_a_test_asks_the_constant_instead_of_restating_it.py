"""A test that copies a constant stops covering it the moment the constant grows.

WHY THIS EXISTS, and the loud version already cost a red suite.

MEASURED 2026-09-06. Three tier-journey tests each hardcoded
``("## Steps", "## Verification", "## Pitfalls")`` — the section list from before
the authoring standard existed. `standard.REQUIRED_SECTIONS` grew to seven and the
three literals did not follow. Bringing the shipped skills UP to the authority
renamed one heading, all three copies broke at once, and the full suite went red
with 13 failures (DEBT-139).

**That was the LUCKY case.** It failed loudly only because a value was RENAMED. A
pure ADDITION is silent: the constant grows, the literal still matches everything
it names, the test still passes, and the new value is simply never covered. Three
such copies were sitting in the tree, found by asking which test literals equal a
src constant exactly:

  * ``_IDENTIFIER_KEYS`` (11 names) — the REDACTION allowlist, restated in
    ``test_observability_redaction``. A twelfth name would have been exempted from
    redaction with nothing testing the exemption was intended.
  * ``DELIVERED_STATUSES`` — restated as a ``parametrize`` list guarding "a
    delivered answer is never requeued", i.e. never sending the user a duplicate.
  * ``_VALID_CLASSES`` — restated in a loop whose docstring promised it "covers all
    three class tokens", a promise a fourth class would silently falsify.

All three now ask the constant. This guard keeps it that way.

DELIBERATELY NARROW. It flags only an EXACT set equality between a literal
collection in a test and a constant in `src/`, and only when the test never mentions
the constant's name. A test that uses SOME of a constant's values as inputs is
normal and is not flagged — the first pass of this measurement reported 26 hits by
counting scattered values, and 24 of them were legitimate. A guard that cries wolf
on correct work is the failure this repo keeps paying for, so the rule is the
narrow one that had zero false positives when measured.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "stackowl"
_TESTS = _ROOT / "tests"

#: Literal collections that MAY mirror a constant, each with its reason. A
#: deliberate pin ("these are exactly the three, and that is the point") is
#: legitimate; it just has to say so here rather than look like an accident.
_DECLARED: dict[str, str] = {}


def _string_set(node: ast.expr) -> frozenset[str]:
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"frozenset", "set", "tuple"}:
        node = node.args[0] if node.args else node
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return frozenset()
    return frozenset(
        e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
    )


@lru_cache(maxsize=1)
def _src_constants() -> dict[str, frozenset[str]]:
    """Module-level UPPERCASE constants holding three or more strings."""
    out: dict[str, frozenset[str]] = {}
    for path in _SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            name = getattr(node.targets[0], "id", "")
            if not name or not name.isupper():
                continue
            values = _string_set(node.value)
            if len(values) >= 3:
                out[name] = values
    return out


def _names_used_in_code(tree: ast.AST) -> frozenset[str]:
    """Every identifier the file actually REFERENCES, ignoring prose.

    A substring search over the source cannot do this, and getting it wrong made
    the first version of this guard survive its own mutant: the fixed test's
    docstring says "Asks `_VALID_CLASSES` rather than restating it", so `name in
    text` was True and the file was skipped even after the literal came back. That
    is the guard-matches-its-own-comment defect this repo has paid for three times
    already — a date regex matching the word "also", and an unbounded-grep guard
    passing because its comment named the very helper it was checking for.
    """
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            used.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            used.update(a.name for a in node.names)
    return frozenset(used)


def _restatements() -> list[str]:
    consts = _src_constants()
    found: list[str] = []
    for path in sorted(_TESTS.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover
            continue
        referenced = _names_used_in_code(tree)
        for node in ast.walk(tree):
            literal = _string_set(node) if isinstance(node, (ast.Tuple, ast.List, ast.Set)) else frozenset()
            if len(literal) < 3:
                continue
            for name, values in consts.items():
                if literal != values or name in referenced:
                    continue
                key = f"{path.relative_to(_ROOT)}:{node.lineno}"
                if key not in _DECLARED:
                    found.append(f"{key} restates {name}")
    return found


class TestNoTestRestatesAConstant:
    @pytest.mark.tripwire
    def test_a_test_asks_the_constant_rather_than_copying_it(self) -> None:
        """An exact copy of a constant is coverage that silently stops growing."""
        offenders = _restatements()

        assert not offenders, (
            "these tests hardcode a set that `src/` already defines, so a value "
            f"ADDED to the constant is silently never covered: {offenders}"
        )

    def test_the_guard_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. If the constant scan returned nothing, the assertion
        above would pass over an empty comparison set."""
        consts = _src_constants()

        assert len(consts) >= 20, f"only found {len(consts)} multi-string constants"
        assert any(len(v) >= 5 for v in consts.values())

    def test_every_declared_exemption_states_a_reason(self) -> None:
        """An exemption list is where this rule would go to die quietly."""
        for key, reason in _DECLARED.items():
            assert len(reason) > 60, f"{key} is exempt without a real reason"
