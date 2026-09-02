"""Test doubles must match the interfaces they stand in for.

FIVE instances of this drift were found in a single session on 2026-08-05, and
DEBT-38 had already named the shape months earlier:

    "a stub that does not track the real interface can hide a genuine assertion
     failure behind a TypeError indefinitely."

What it cost:

  * FakeKuzu.execute lacked ``budget_s`` (added to the real handler by DEBT-19)
    -> six dream-worker tests dead on a TypeError.
  * _FakeProviderRegistry.get_by_tier / get_with_cascade returned a bare object
    where the real ones return ``tuple[ModelProvider, str]``
    -> "cannot unpack non-iterable _ScriptedSecretary object".
  * The parliament registry's resolve_capable_or_degrade returned a 2-tuple
    where the real one has returned 3 since F125
    -> "not enough values to unpack (expected 3, got 2)", 13 tests.
  * _StubOrchestrator.run took ``session_key`` where the real orchestrator takes
    ``conversation_id`` -> the command surfaced an error string instead of a result.

Every one of them failed for a reason that had NOTHING to do with what the test
asserted, and every one sat red long enough to be assumed normal.

WHAT THIS CHECKS, and why only this. Two things broke in practice: the
PARAMETERS a double accepts, and the ARITY of the tuple it returns. So those are
what is verified. It deliberately does not demand identical type annotations — a
double legitimately returns its own scripted type — because a check that forces
doubles to lie about their types would be worse than the drift it prevents.

ADDING A PAIR IS THE POINT. When you write a double for something real, add it
to ``_PAIRS`` below. The cost is one line; the alternative is measured above.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable

import pytest

# CROSS-CUTTING GUARD. This protects a property of the WHOLE repo, so a
# per-item test run never selects it — which is how two real bypasses
# shipped (an unscoped task_outcomes read, and three stale allowlist
# entries for deleted modules). `scripts/tripwires.sh` runs everything
# marked this way, whatever the change touched.
pytestmark = pytest.mark.tripwire


def _tuple_arity(annotation: object) -> int | None:
    """Arity of a ``tuple[...]`` annotation, or None if it isn't one.

    String annotations (``from __future__ import annotations`` is on almost
    everywhere) are handled textually — resolving them would need each module's
    namespace and would fail on TYPE_CHECKING-only imports, which is precisely
    where these interfaces live.
    """
    if isinstance(annotation, str):
        text = annotation.strip()
        if not text.startswith("tuple["):
            return None
        inner = text[len("tuple[") : -1]
        depth = 0
        parts = 1
        for ch in inner:
            if ch in "[(":
                depth += 1
            elif ch in "])":
                depth -= 1
            elif ch == "," and depth == 0:
                parts += 1
        # tuple[X, ...] is variadic — no fixed arity to compare.
        return None if inner.rstrip().endswith("...") else parts
    origin = typing.get_origin(annotation)
    if origin is not tuple:
        return None
    args = typing.get_args(annotation)
    return None if (len(args) == 2 and args[1] is Ellipsis) else len(args)


def _params(func: Callable[..., object]) -> set[str]:
    return {
        name for name, p in inspect.signature(func).parameters.items()
        if name not in ("self", "cls")
        and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    }


def _accepts_anything(func: Callable[..., object]) -> bool:
    """A double using *args/**kwargs opts out — it cannot drift on parameters."""
    kinds = {p.kind for p in inspect.signature(func).parameters.values()}
    return {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD} <= kinds


def _real_provider_registry() -> object:
    from stackowl.providers.registry import ProviderRegistry

    return ProviderRegistry


def _pairs() -> list[tuple[str, object, object, tuple[str, ...]]]:
    """(label, double, real, methods) — imported lazily so a collection error in
    one test module cannot take the whole conformance check down with it."""
    from stackowl.parliament.orchestrator import ParliamentOrchestrator

    # FakeKuzu/KuzuSyncJobHandler pair REMOVED 2026-09-01: the real handler was
    # deleted with the rest of the retired fact-store machinery, and a
    # conformance check against a class that no longer exists is the double
    # outliving the thing it doubled — exactly what this file exists to catch.
    real_registry = _real_provider_registry()
    out: list[tuple[str, object, object, tuple[str, ...]]] = []

    # These live inside test modules; import defensively so a module-level
    # failure elsewhere surfaces as its own test failure, not as a missing check.
    try:
        from tests.journeys.test_j_durable_goal import _FakeProviderRegistry

        out.append((
            "_FakeProviderRegistry", _FakeProviderRegistry, real_registry,
            ("get", "get_by_tier", "get_with_cascade"),
        ))
    except Exception:  # noqa: BLE001 — reported by that module's own tests
        pass
    try:
        from tests.test_story_5_4 import _StubOrchestrator

        out.append((
            "_StubOrchestrator", _StubOrchestrator, ParliamentOrchestrator, ("run",),
        ))
    except Exception:  # noqa: BLE001
        pass
    return out


def _cases() -> list[tuple[str, object, object, str]]:
    return [
        (label, double, real, method)
        for label, double, real, methods in _pairs()
        for method in methods
    ]


@pytest.mark.parametrize(
    ("label", "double", "real", "method"),
    _cases(),
    ids=[f"{label}.{m}" for label, _d, _r, m in _cases()],
)
def test_double_accepts_every_parameter_the_real_one_does(label, double, real, method):
    """The FakeKuzu/budget_s and session_key/conversation_id failures, in one check."""
    d_func = getattr(double, method, None)
    assert d_func is not None, f"{label} does not implement {method}"
    if _accepts_anything(d_func):
        return
    missing = _params(getattr(real, method)) - _params(d_func)
    assert not missing, (
        f"{label}.{method} is missing parameter(s) {sorted(missing)} that the real "
        f"{real.__name__}.{method} accepts — callers passing them will die on a "
        f"TypeError that looks like a failure of whatever the test asserts"
    )


@pytest.mark.parametrize(
    ("label", "double", "real", "method"),
    _cases(),
    ids=[f"{label}.{m}" for label, _d, _r, m in _cases()],
)
def test_double_returns_the_same_tuple_shape(label, double, real, method):
    """The 2-tuple-vs-3-tuple failures. Only checked when the real method
    annotates a fixed-arity tuple — that is the case that unpacks at the call
    site and therefore the case that breaks."""
    real_arity = _tuple_arity(
        inspect.signature(getattr(real, method)).return_annotation
    )
    if real_arity is None:
        pytest.skip(f"{real.__name__}.{method} does not return a fixed-arity tuple")
    d_func = getattr(double, method)
    double_arity = _tuple_arity(inspect.signature(d_func).return_annotation)
    assert double_arity == real_arity, (
        f"{label}.{method} returns a {double_arity}-tuple but the real "
        f"{real.__name__}.{method} returns a {real_arity}-tuple — the call site "
        f"unpacks, so this fails as 'not enough values to unpack'"
    )


def test_there_is_at_least_one_pair_registered():
    """A conformance suite that silently checks nothing is worse than none: it
    reports green while the drift it exists to catch happens freely."""
    assert _pairs(), "no (double, real) pairs registered — this suite is inert"
