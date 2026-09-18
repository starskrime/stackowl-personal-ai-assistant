"""Startup wiring guard -- Spec 2.4's ``_accept_core`` must check the peer's
PID BEFORE trusting the connection with ``gateway_link.set_connection(...)``.

THE BUG THIS GUARDS AGAINST: a peer-PID check added AFTER `set_connection`
(or after the Hello send) would still close the historical gap this story
fixes -- an impostor would already have received the gateway's Hello (a
real, if small, information leak) and briefly displaced the real link. The
ONLY way this refuses "before any protocol information is sent" (the
Boundaries contract) is source ORDER: `link_auth.authorize_peer(...)` must
appear, textually, before `gateway_link.set_connection(...)` inside
`_accept_core`.

WHY AN AST GUARD, not a full boot: same rationale as
``tests/startup/test_core_hello_wiring.py`` -- `_accept_core` is nested
inside `_phase_gateway`, a single monolithic coroutine that opens the real
``~/.stackowl`` DB and blocks forever accepting connections, so a full boot
is impractical in a bounded test. This guard parses the AST of
`_phase_gateway`, finds the nested `_accept_core` function, and asserts the
line number of the `authorize_peer(...)` call precedes the line number of
the `set_connection(...)` call.
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


def _find_call_by_attr(fn: ast.AST, attr: str) -> ast.Call:
    """Find the single ``Call`` whose callee is a dotted attribute named ``attr``
    (e.g. ``link_auth.authorize_peer`` or ``gateway_link.set_connection``)."""
    matches = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == attr
    ]
    assert len(matches) == 1, (
        f"expected exactly one call to '...{attr}(...)' in _accept_core, found "
        f"{len(matches)}"
    )
    return matches[0]


def test_accept_core_checks_the_peer_pid_before_trusting_the_connection() -> None:
    """``authorize_peer(...)`` must appear (source order) before
    ``set_connection(...)`` -- an impostor must get ZERO protocol information."""
    phase_gateway = _phase_gateway_ast()
    accept_core = _find_nested_function(phase_gateway, "_accept_core")

    authorize_call = _find_call_by_attr(accept_core, "authorize_peer")
    set_connection_call = _find_call_by_attr(accept_core, "set_connection")

    assert authorize_call.lineno < set_connection_call.lineno, (
        "_accept_core must call link_auth.authorize_peer(...) BEFORE "
        "gateway_link.set_connection(...) -- an impostor connection must be "
        "refused before it receives ANY Hello/protocol information"
    )


def test_accept_core_raises_link_authentication_error_on_a_peer_mismatch() -> None:
    """The refusal must be a real, raised ``LinkAuthenticationError`` -- not a
    logged-and-continued warning (which would still call `set_connection`)."""
    phase_gateway = _phase_gateway_ast()
    accept_core = _find_nested_function(phase_gateway, "_accept_core")

    raises = [
        n
        for n in ast.walk(accept_core)
        if isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and isinstance(n.exc.func, ast.Name)
        and n.exc.func.id == "LinkAuthenticationError"
    ]
    assert len(raises) == 1, (
        "_accept_core must raise exactly one LinkAuthenticationError on a "
        f"peer-PID mismatch, found {len(raises)}"
    )
