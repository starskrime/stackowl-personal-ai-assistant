"""Startup wiring guard -- ``_core_frame_loop``'s ``HelloFrame`` branch must
call ``evaluate_hello(..., local_is_core=True)`` (Spec 2.3).

THE BUG THIS GUARDS AGAINST: ``_core_frame_loop`` is the ONLY real production
call site that evaluates the gateway's Hello from the CORE's own point of
view. ``GatewayLink._route`` makes the mirror call with
``local_is_core=False`` on the exact same ``evaluate_hello`` signature --
a plausible copy/paste slip (accidentally flipping the literal here to
``False``) would silently INVERT which side is blamed on every digest-only
split (Spec 2.3's tiebreak: the gateway is normally the older side, but a
flipped flag would blame the core instead), with unit tests on the pure
`evaluate_hello` helper itself giving no signal at all -- they don't know
this call site exists.

WHY AN AST GUARD, not a full boot: same rationale as
``tests/startup/test_journal_name_resolver_wiring.py`` -- ``_core_frame_loop``
is nested inside ``_phase_gateway``, a single monolithic coroutine that opens
the real ``~/.stackowl`` DB and blocks forever in a frame loop, so a full
boot is impractical in a bounded test. This guard parses the AST of
``_phase_gateway``, finds the nested ``_core_frame_loop`` function, and
asserts its ``evaluate_hello(...)`` call carries the literal keyword
``local_is_core=True``.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from stackowl.startup import orchestrator as orch_mod


def _phase_gateway_ast() -> ast.AsyncFunctionDef:
    """Return the parsed AST of ``StartupOrchestrator._phase_gateway``."""
    src = textwrap.dedent(inspect.getsource(orch_mod.StartupOrchestrator._phase_gateway))
    mod = ast.parse(src)
    fn = mod.body[0]
    assert isinstance(fn, ast.AsyncFunctionDef), "expected _phase_gateway to be an async def"
    return fn


def _find_nested_function(fn: ast.AST, name: str) -> ast.AsyncFunctionDef:
    """Find the single nested (nested anywhere inside ``fn``) async def ``name``."""
    matches = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == name
    ]
    assert len(matches) == 1, (
        f"expected exactly one nested async def '{name}' inside _phase_gateway, "
        f"found {len(matches)}"
    )
    return matches[0]


def _find_call_by_name(fn: ast.AST, name: str) -> ast.Call:
    """Find the single top-level-named ``Call`` (a bare ``name(...)``, not a
    dotted attribute chain) in ``fn``."""
    matches = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]
    assert len(matches) == 1, (
        f"expected exactly one call to '{name}' in _core_frame_loop, found "
        f"{len(matches)}"
    )
    return matches[0]


def test_core_frame_loop_evaluates_its_own_hello_as_the_core_side() -> None:
    """``_core_frame_loop``'s ``HelloFrame`` branch calls ``evaluate_hello``
    with the literal keyword ``local_is_core=True`` -- structural guard (see
    module docstring for why AST and not a full boot)."""
    phase_gateway = _phase_gateway_ast()
    core_frame_loop = _find_nested_function(phase_gateway, "_core_frame_loop")
    call = _find_call_by_name(core_frame_loop, "evaluate_hello")

    kw = {k.arg: k.value for k in call.keywords}
    assert "local_is_core" in kw, (
        "evaluate_hello(...) in _core_frame_loop must pass local_is_core= explicitly"
    )
    value = kw["local_is_core"]
    assert isinstance(value, ast.Constant) and value.value is True, (
        "_core_frame_loop must call evaluate_hello(..., local_is_core=True) -- "
        "it is the CORE's own evaluation of the gateway's Hello; a flipped "
        "literal here silently inverts which side is blamed on every "
        "digest-only split"
    )


def test_core_frame_loop_reacts_via_react_to_core_hello_verdict() -> None:
    """The verdict from ``evaluate_hello`` must actually be threaded into
    ``react_to_core_hello_verdict`` -- otherwise the compatibility check is
    computed and silently discarded."""
    phase_gateway = _phase_gateway_ast()
    core_frame_loop = _find_nested_function(phase_gateway, "_core_frame_loop")
    react_call = _find_call_by_name(core_frame_loop, "react_to_core_hello_verdict")

    first_arg = react_call.args[0] if react_call.args else None
    assert isinstance(first_arg, ast.Name) and first_arg.id == "verdict", (
        "react_to_core_hello_verdict must be called with the SAME `verdict` "
        "evaluate_hello just returned"
    )
