"""Startup wiring guard -- ``_core_frame_loop``'s ``TasksEnqueuedFrame``
branch must call ``.wake()`` on the local task loop (Story 4.3, AD-1).

WHY AN AST GUARD, not a full boot -- same rationale as
``tests/startup/test_core_hello_wiring.py``: ``_core_frame_loop`` is nested
inside ``_phase_gateway``, a single monolithic coroutine that opens the real
``~/.stackowl`` DB and blocks forever in a frame loop, so a full boot is
impractical in a bounded test. This guard parses the AST of
``_phase_gateway``, finds the nested ``_core_frame_loop`` function, and
asserts its ``isinstance(frame, TasksEnqueuedFrame)`` branch actually calls
``.wake()`` -- proving the gateway->core wake frame this story adds (AC:
"a gateway-side enqueue sends the payload-free TasksEnqueuedFrame") does
something once it arrives, not just that the frame type exists.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from stackowl.startup import orchestrator as orch_mod


def _phase_gateway_ast() -> ast.AsyncFunctionDef:
    src = textwrap.dedent(inspect.getsource(orch_mod.StartupOrchestrator._phase_gateway))
    mod = ast.parse(src)
    fn = mod.body[0]
    assert isinstance(fn, ast.AsyncFunctionDef), "expected _phase_gateway to be an async def"
    return fn


def _find_nested_function(fn: ast.AST, name: str) -> ast.AsyncFunctionDef:
    matches = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == name
    ]
    assert len(matches) == 1, (
        f"expected exactly one nested async def '{name}' inside _phase_gateway, "
        f"found {len(matches)}"
    )
    return matches[0]


def _find_isinstance_branch(fn: ast.AST, type_name: str) -> ast.If:
    """The single ``if isinstance(frame, <type_name>): ...`` branch."""
    matches: list[ast.If] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name) and test.func.id == "isinstance"
            and len(test.args) == 2
            and isinstance(test.args[1], ast.Name) and test.args[1].id == type_name
        ):
            matches.append(node)
    assert len(matches) == 1, (
        f"expected exactly one 'isinstance(frame, {type_name})' branch, "
        f"found {len(matches)}"
    )
    return matches[0]


def test_core_frame_loop_wakes_the_task_loop_on_tasks_enqueued() -> None:
    phase_gateway = _phase_gateway_ast()
    core_frame_loop = _find_nested_function(phase_gateway, "_core_frame_loop")
    branch = _find_isinstance_branch(core_frame_loop, "TasksEnqueuedFrame")

    wake_calls = [
        n for n in ast.walk(branch)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == "wake"
    ]
    assert wake_calls, (
        "the TasksEnqueuedFrame branch in _core_frame_loop must call "
        ".wake() on the local task loop"
    )


def test_the_branch_detector_actually_catches_a_missing_wake_call() -> None:
    """Self-check (red/green proof): a branch that does NOT call .wake() is
    correctly reported as having none, so the real assertion above is not
    vacuously true."""
    src = (
        "async def _core_frame_loop():\n"
        "    if isinstance(frame, TasksEnqueuedFrame):\n"
        "        log.info('got one')\n"
        "        continue\n"
    )
    tree = ast.parse(src)
    fn = tree.body[0]
    branch = _find_isinstance_branch(fn, "TasksEnqueuedFrame")
    wake_calls = [
        n for n in ast.walk(branch)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == "wake"
    ]
    assert wake_calls == []
