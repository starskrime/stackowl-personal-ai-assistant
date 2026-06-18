"""Startup wiring guard — `scheduler.recover()` must run ONCE at boot.

THE BUG THIS GUARDS (already diagnosed): ``JobScheduler.recover()`` reaps jobs a
crash left in ``status='running'`` back to runnable and replays/realarms overdue
ones. The poller only ever selects ``status='pending'`` rows, so a job left
``'running'`` by a crash wedges forever unless ``recover()`` runs at startup.
``recover()`` was only ever called by tests — never by the orchestrator — so an
assigned task did NOT survive a restart. The fix wires ``recover()`` into
``StartupOrchestrator._phase_gateway`` exactly once, immediately before the
supervisor poll loop starts.

WHY THIS IS A STRUCTURAL (AST) GUARD, not a full boot:
  A full orchestrator boot is impractical in a bounded test — ``_phase_gateway``
  is a single monolithic coroutine that opens the real ~/.stackowl DB, builds
  Kuzu/LanceDB/providers/browser/TUI/notification/scheduler collaborators, and
  then BLOCKS forever in ``await adapter.run()`` (the message loop). The existing
  orchestrator-driving guard
  (``tests/journeys/test_memory_fix_guards.py::test_guard_memory_command_registered_via_orchestrator``)
  reaches only ~line 287 (it halts at ``NotificationAssembly.build`` via a
  sentinel) — well BEFORE the scheduler assembly (~line 583) and the supervisor
  start (~line 983). Extending that sentinel technique to reach line 983 would
  require stubbing ~15 more heavy collaborators (browser runtime, consent gate,
  clarify gateway+classifier, cost trackers, event handlers, TUI assembly, MCP
  server, the CLI/Telegram adapters) AND neutering the blocking ``adapter.run()``
  loop — a fragile mega-stub that would break on any unrelated boot refactor and
  prove little. Per the task's guard-selection guidance, when a runtime guard
  cannot be made non-fragile, the strongest feasible alternative is a structural
  guard against silent removal/reordering.

  This guard therefore parses the AST of ``_phase_gateway`` and asserts the three
  load-bearing invariants of the fix:
    1. ``_phase_gateway`` calls ``scheduler...recover()`` (an awaited call);
    2. that ``recover()`` call lexically PRECEDES the ``supervisor...start()``
       call (recover must run before the poll loop, else a reaped/replayed job is
       not yet runnable when the loop ticks);
    3. the ``recover()`` call is inside a ``try`` whose ``except`` does NOT
       re-raise (fail-OPEN: a recovery error must not abort startup).
  Reverting the fix (deleting the recover() call, moving it after the loop start,
  or wrapping it so an error propagates) makes the corresponding assertion FAIL.
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


def _attr_chain(node: ast.AST) -> str:
    """Render a dotted attribute/name chain (e.g. ``a.b.c``) for matching."""
    parts: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _find_call_by_chain_suffix(fn: ast.AST, suffix: str) -> ast.Call:
    """Find the single ``Call`` whose dotted attribute chain ends with ``suffix``.

    ``suffix`` is a dotted tail like ``"scheduler.recover"`` or
    ``"supervisor.start"`` — this disambiguates from other same-named methods in
    the (large) _phase_gateway body (e.g. ``browser_runtime.start``).
    """
    matches = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and _attr_chain(n.func).endswith(suffix)
    ]
    assert len(matches) == 1, (
        f"expected exactly one call whose chain ends with '{suffix}' in "
        f"_phase_gateway, found {len(matches)}: "
        f"{[_attr_chain(m.func) for m in matches]!r}"
    )
    return matches[0]


def test_phase_gateway_calls_scheduler_recover_before_supervisor_start() -> None:
    """recover() is wired into _phase_gateway, before the poll loop, fail-open.

    Structural guard (see module docstring for why AST and not a full boot).
    Asserts the three invariants of the restart-survival fix.
    """
    fn = _phase_gateway_ast()

    # (1) recover() is called at all, on the scheduler, and awaited.
    recover_call = _find_call_by_chain_suffix(fn, "scheduler.recover")
    # It must be awaited (recover() is an async coroutine returning the replay count).
    awaited = any(
        isinstance(n, ast.Await) and isinstance(n.value, ast.Call) and n.value is recover_call
        for n in ast.walk(fn)
    )
    assert awaited, "scheduler.recover() must be awaited"

    # (2) recover() runs BEFORE supervisor.start() (reap/replay before the loop).
    start_call = _find_call_by_chain_suffix(fn, "supervisor.start")
    assert recover_call.lineno < start_call.lineno, (
        "scheduler.recover() must be called BEFORE supervisor.start() so a reaped/"
        "replayed job is runnable before the poll loop ticks. recover at line "
        f"{recover_call.lineno}, supervisor.start at line {start_call.lineno}."
    )

    # (3) recover() is inside a try/except that does NOT re-raise (fail-open: a
    # recovery error must never abort startup).
    enclosing_try: ast.Try | None = None
    for node in ast.walk(fn):
        # The try whose BODY (not its except/else/finally) directly contains the
        # recover() call — so an outer try wrapping the start() call can't give a
        # false pass.
        if isinstance(node, ast.Try) and any(
            c is recover_call for b in node.body for c in ast.walk(b)
        ):
            enclosing_try = node
    assert enclosing_try is not None, (
        "scheduler.recover() must be wrapped in a try/except so a recovery failure "
        "does NOT abort startup (fail-open)."
    )
    assert enclosing_try.handlers, "the try around recover() must have an except handler"
    for handler in enclosing_try.handlers:
        for sub in ast.walk(handler):
            assert not isinstance(sub, ast.Raise), (
                "the except handler around scheduler.recover() must NOT re-raise — a "
                "recovery error must fail OPEN and let startup continue."
            )
