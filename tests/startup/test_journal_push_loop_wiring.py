"""Startup wiring guard -- Spec 2.5: the CORE-role branch must actually start
``_journal_push_loop()``, and the GATEWAY-role ``GatewayLink(...)``
construction must carry ``journal_fetcher=`` (otherwise a Hello reconnect's
catch-up read has nothing to read from).

WHY AN AST GUARD, not a full boot: mirrors ``tests/startup/test_core_hello_wiring.py``
-- ``_phase_gateway`` is a single monolithic coroutine that opens the real
``~/.stackowl`` DB and blocks forever in a frame loop, so a full boot is
impractical in a bounded unit test. Both nested-function existence
(``_journal_push_loop``) and its use are asserted structurally.
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


def test_journal_push_loop_is_defined_inside_phase_gateway() -> None:
    """``_journal_push_loop`` exists as a sibling of ``_core_frame_loop`` --
    fails loudly (rather than silently passing) if it is ever renamed or
    moved out of ``_phase_gateway`` without updating this guard."""
    phase_gateway = _phase_gateway_ast()
    _find_nested_function(phase_gateway, "_journal_push_loop")  # raises on failure


def test_the_core_role_branch_starts_the_journal_push_loop() -> None:
    """The CORE-role branch (guarded by ``self._role == "core"``) must call
    ``_supervise_channel("journal_push", _journal_push_loop)`` — the same
    Supervisor/``make_supervised_task`` mechanism the four channel-receive
    loops use (backoff restart, a consecutive-failure ceiling, a stuck-task
    watchdog), so an exception escaping the loop's body is caught and
    restarted rather than killing it silently and permanently. Declaring the
    coroutine with nothing ever scheduling it would leave split-mode TUI
    progress dark exactly like the ``ProgressEventFrame`` defect this story
    fixes."""
    phase_gateway = _phase_gateway_ast()

    supervise_calls_of_journal_push_loop = [
        call
        for call in ast.walk(phase_gateway)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_supervise_channel"
        and len(call.args) >= 2
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "journal_push"
        and isinstance(call.args[1], ast.Name)
        and call.args[1].id == "_journal_push_loop"
    ]
    assert len(supervise_calls_of_journal_push_loop) == 1, (
        'expected exactly one _supervise_channel("journal_push", _journal_push_loop) '
        f"call in _phase_gateway, found {len(supervise_calls_of_journal_push_loop)}"
    )

    # And it must be reached only via an `if self._role == "core":` guard —
    # never unconditionally (mono/gateway must never construct this task).
    call = supervise_calls_of_journal_push_loop[0]
    guarding_ifs = [
        n
        for n in ast.walk(phase_gateway)
        if isinstance(n, ast.If)
        and call in ast.walk(n)
        and _is_role_equals_core_test(n.test)
    ]
    assert guarding_ifs, (
        '_supervise_channel("journal_push", _journal_push_loop) must be reached '
        'only through an `if self._role == "core":` guard'
    )

    # And no bare `asyncio.create_task(_journal_push_loop())` should remain —
    # the review fix replaced that path with the supervised registration above.
    bare_create_task_calls = [
        call
        for call in ast.walk(phase_gateway)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "create_task"
        and call.args
        and isinstance(call.args[0], ast.Call)
        and isinstance(call.args[0].func, ast.Name)
        and call.args[0].func.id == "_journal_push_loop"
    ]
    assert not bare_create_task_calls, (
        "_journal_push_loop must be started only through the supervised "
        "_supervise_channel(...) registration, not a bare asyncio.create_task(...)"
    )


def _is_role_equals_core_test(test: ast.expr) -> bool:
    """True for the AST of ``self._role == "core"`` specifically."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left = test.left
    is_self_role = (
        isinstance(left, ast.Attribute)
        and left.attr == "_role"
        and isinstance(left.value, ast.Name)
        and left.value.id == "self"
    )
    right = test.comparators[0]
    is_core_literal = isinstance(right, ast.Constant) and right.value == "core"
    return is_self_role and is_core_literal


def test_the_gateway_role_gatewaylink_construction_carries_a_journal_fetcher() -> None:
    """The GATEWAY-role ``GatewayLink(...)`` construction must pass
    ``journal_fetcher=`` — otherwise a Hello reconnect's ``_catch_up_journal``
    silently no-ops forever (``self._journal_fetcher is None``), and AC3
    ("no loss or duplication across a real restart") is unmet in production
    even though every unit test around it passes with a fake fetcher."""
    phase_gateway = _phase_gateway_ast()

    gateway_link_calls = [
        n
        for n in ast.walk(phase_gateway)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "GatewayLink"
    ]
    assert len(gateway_link_calls) == 1, (
        f"expected exactly one GatewayLink(...) construction, found {len(gateway_link_calls)}"
    )
    call = gateway_link_calls[0]
    kw_names = {kw.arg for kw in call.keywords}
    assert "journal_fetcher" in kw_names, (
        "GatewayLink(...) must pass journal_fetcher= so a Hello reconnect's "
        "catch-up read has something to read from (Spec 2.5, AC3)"
    )
