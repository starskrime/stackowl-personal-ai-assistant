"""A hand-written provider double that drifts from the ABC fails 31 minutes late.

WHAT THIS COST, measured 2026-09-05. Threading one new keyword through
`ModelProvider.complete_with_tools` (`token_budget_fn`, ESC-147) broke NINE
hand-written doubles across five files. Targeted runs did not see it:
`tests/providers` + `tests/pipeline` (2,604 passed), then `tests/tools` +
`tests/smoke` (1,989 passed) were all green, because seven of the nine live in
`tests/journeys/` — a directory none of those paths touch. The full suite found them
in **1,853 seconds**, and the failure they produced was a bare
``TypeError: got an unexpected keyword argument`` surfacing as
``goal failed: execute: TypeError``, which reads like a product bug rather than a
stale fixture.

This is CLAUDE.md shape #2 — "test doubles that stopped resembling the real thing" —
and the doubles themselves knew: every one carries the comment
``# F027/SP-4 — match the real signature`` from the LAST time someone threaded a
keyword through and had to chase them all down. The comment records the obligation
and nothing enforced it.

SO THE GUARD IS THE ENFORCEMENT, and it runs in the ~40-second gate rather than the
31-minute suite. A double may satisfy it two ways: accept ``**kwargs`` (tolerant, and
then it can never drift), or name every parameter the ABC names (explicit, and then
this test says so the moment the ABC gains another).

WHY NOT JUST MANDATE ``**kwargs`` EVERYWHERE. Because several of these doubles assert
on what they were passed, and a tolerant signature would swallow a caller that stopped
passing something. The choice is deliberately left to each double; only DRIFT is
forbidden.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ABC = _ROOT / "src" / "stackowl" / "providers" / "base.py"


def _params_of(node: ast.AsyncFunctionDef | ast.FunctionDef) -> tuple[set[str], bool]:
    """(named parameters, accepts **kwargs)."""
    a = node.args
    names = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)} - {"self"}
    return names, a.kwarg is not None


def _find(path: pathlib.Path, name: str) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == name
    ]


def _abc_params() -> set[str]:
    defs = _find(_ABC, "complete_with_tools")
    assert defs, "complete_with_tools vanished from the provider ABC"
    names, _ = _params_of(defs[0])
    return names


@pytest.mark.tripwire
def test_every_provider_double_still_matches_the_contract() -> None:
    """The whole point: catch a stale double in the gate, not in the 31-minute run."""
    required = _abc_params()
    assert len(required) > 5, f"the ABC parse found only {required} — the parser is wrong"

    drifted: list[str] = []
    checked = 0
    for path in sorted((_ROOT / "tests").rglob("*.py")):
        for fn in _find(path, "complete_with_tools"):
            checked += 1
            names, tolerant = _params_of(fn)
            if tolerant:
                continue
            missing = required - names
            if missing:
                rel = path.relative_to(_ROOT)
                drifted.append(f"{rel}:{fn.lineno} missing {sorted(missing)}")

    assert checked >= 5, (
        f"only {checked} provider doubles found — the scan is not reaching them, "
        "which would make this guard silently vacuous"
    )
    assert not drifted, (
        "provider double(s) no longer accept what the ABC passes:\n  "
        + "\n  ".join(drifted)
        + "\n\nEither add the parameter, or give the double **kwargs. Left alone this "
        "surfaces as a bare TypeError from deep inside a journey test, 31 minutes into "
        "the full suite."
    )


def test_the_guard_would_notice_a_missing_parameter() -> None:
    """A guard nobody has seen fail is a guard nobody knows works. This proves the
    comparison is real by running it against a signature that IS missing something."""
    required = _abc_params()
    fake = ast.parse(
        "async def complete_with_tools(self, user_text): ...\n"
    ).body[0]
    names, tolerant = _params_of(fake)  # type: ignore[arg-type]

    assert not tolerant
    assert required - names, "a one-parameter stub was judged complete — the check is vacuous"


def test_a_tolerant_double_is_accepted() -> None:
    """`**kwargs` is a legitimate answer, so the guard must not force enumeration."""
    fake = ast.parse(
        "async def complete_with_tools(self, user_text, **kwargs): ...\n"
    ).body[0]
    _, tolerant = _params_of(fake)  # type: ignore[arg-type]
    assert tolerant
