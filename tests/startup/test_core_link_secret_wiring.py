"""Startup wiring guard -- Spec 2.4: the core role's real Hello send must
carry this boot's link secret via ``link_auth.read_link_secret_from_env()``.

THE BUG THIS GUARDS AGAINST: ``_phase_gateway``'s core-role branch builds and
sends the ONE real Hello this core process ever emits (``_core_frame_loop``'s
own ``build_local_hello`` call is a LOCAL, comparison-only Hello -- never
sent, and Spec 2.3's ``evaluate_hello`` never even looks at its
``link_secret``). If a future edit dropped the ``link_secret=`` keyword here
(or sourced it from anything other than the env this core was
spawned/respawned/execv'd with), every real core would silently fail Spec
2.4's secret check -- and since that check lives inside
``GatewayLink._route``, not ``evaluate_hello``, no existing Hello-compat test
would catch it.

WHY AN AST GUARD, not a full boot: same rationale as
``tests/startup/test_core_hello_wiring.py``/``test_peer_pid_wiring.py`` --
``_phase_gateway`` is a monolithic coroutine that opens the real
``~/.stackowl`` DB and blocks forever, so a full boot is impractical in a
bounded test. This guard parses the AST of ``_phase_gateway``, restricts
itself to calls made DIRECTLY in its body (never descending into a nested
function, e.g. ``_accept_core`` or ``_core_frame_loop``, which have their own
unrelated ``build_local_hello`` calls), and asserts the one
``build_local_hello(...)`` call found there carries a ``link_secret=``
keyword whose value is exactly ``link_auth.read_link_secret_from_env()``.
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


class _TopLevelCallCollector(ast.NodeVisitor):
    """Collects ``Call`` nodes made directly in ``root``'s body -- never
    descending into a nested function def (sync or async), so a nested
    helper's own calls (e.g. ``_accept_core``'s or ``_core_frame_loop``'s own
    ``build_local_hello`` calls) are excluded."""

    def __init__(self, root: ast.AST) -> None:
        self.root = root
        self.calls: list[ast.Call] = []
        self._in_root = False

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            self._in_root = True
            self.generic_visit(node)
            self._in_root = False
        # else: a nested async def (e.g. _accept_core, _core_frame_loop) --
        # deliberately NOT descended into.

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # a nested sync def -- deliberately NOT descended into.

    def visit_Call(self, node: ast.Call) -> None:
        if self._in_root:
            self.calls.append(node)
        self.generic_visit(node)


def _find_top_level_call_by_name(fn: ast.AsyncFunctionDef, name: str) -> ast.Call:
    collector = _TopLevelCallCollector(fn)
    collector.visit(fn)
    matches = [
        c for c in collector.calls
        if isinstance(c.func, ast.Name) and c.func.id == name
    ]
    assert len(matches) == 1, (
        f"expected exactly one TOP-LEVEL call to '{name}' in _phase_gateway "
        f"(outside any nested function), found {len(matches)}"
    )
    return matches[0]


def test_core_role_sends_its_real_hello_with_the_link_secret_from_env() -> None:
    """The core-role branch's real (sent) Hello must carry
    ``link_secret=link_auth.read_link_secret_from_env()`` -- not a bare
    default/``None`` and not sourced from anywhere else."""
    phase_gateway = _phase_gateway_ast()
    call = _find_top_level_call_by_name(phase_gateway, "build_local_hello")

    kw = {k.arg: k.value for k in call.keywords}
    assert "link_secret" in kw, (
        "the core role's real build_local_hello(...) call must pass "
        "link_secret= explicitly"
    )
    value = kw["link_secret"]
    assert (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "read_link_secret_from_env"
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "link_auth"
    ), (
        "the core role's real Hello must source link_secret from "
        "link_auth.read_link_secret_from_env() -- the per-boot secret THIS "
        "core process was spawned/respawned/execv'd with"
    )
