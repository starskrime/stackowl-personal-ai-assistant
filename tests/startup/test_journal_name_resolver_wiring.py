"""Startup wiring guard -- ``register_task_name_resolver(db_pool)`` must run
ONCE at boot, inside ``StartupOrchestrator._phase_gateway`` (Story 2.2, AD-30).

THE BUG THIS GUARDS AGAINST: if this call is silently removed or reordered
before ``db_pool`` exists, ``journal/narrator.py`` never gets a
``NameResolver`` for ``RecordKind.TASK`` -- every task event then tombstones
forever (``"a retired task"``) in every surface that calls ``journal.narrate()``,
with a fully green test suite everywhere else, because the tombstone path is
itself a valid, never-raising code path.

WHY AN AST GUARD, not a full boot: same rationale as
``tests/scheduler/test_recover_startup_wiring.py`` -- ``_phase_gateway`` is a
single monolithic coroutine that opens the real ``~/.stackowl`` DB and
eventually blocks forever in the message loop, so a full boot is impractical
in a bounded test. This guard parses the AST of ``_phase_gateway`` and asserts
the load-bearing invariants of the wiring:
  1. ``register_task_name_resolver`` is called, unconditionally (not inside an
     ``if``/``try`` that could skip it silently);
  2. its argument is the same ``db_pool`` name the function binds via
     ``db_pool = DbPool(...)`` -- not some other object;
  3. the call happens AFTER that ``db_pool`` assignment (an unopened/unbound
     pool would be a construction-order bug).
Reverting the fix (deleting the call, or moving it above the ``db_pool``
assignment) makes the corresponding assertion FAIL.
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


def _find_call_by_name(fn: ast.AST, name: str) -> ast.Call:
    """Find the single top-level-named ``Call`` (a bare ``name(...)``, not a
    dotted attribute chain) in ``fn``."""
    matches = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]
    assert len(matches) == 1, (
        f"expected exactly one call to '{name}' in _phase_gateway, found "
        f"{len(matches)}"
    )
    return matches[0]


def _find_assignment_lineno(fn: ast.AST, target_name: str) -> int:
    """Return the line number of the (single) ``target_name = ...`` assignment."""
    matches = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == target_name for t in n.targets)
    ]
    assert len(matches) == 1, (
        f"expected exactly one assignment to '{target_name}' in _phase_gateway, "
        f"found {len(matches)}"
    )
    return matches[0].lineno


def test_phase_gateway_registers_the_task_name_resolver_after_db_pool_exists() -> None:
    """``register_task_name_resolver(db_pool)`` is wired into ``_phase_gateway``,
    unconditionally, after ``db_pool`` is bound.

    Structural guard (see module docstring for why AST and not a full boot).
    """
    fn = _phase_gateway_ast()

    call = _find_call_by_name(fn, "register_task_name_resolver")

    # (1) called with exactly the `db_pool` name the function binds -- not a
    # different object, not a literal, not an attribute of something else.
    assert len(call.args) == 1, "register_task_name_resolver must take exactly one argument"
    arg = call.args[0]
    assert isinstance(arg, ast.Name) and arg.id == "db_pool", (
        f"register_task_name_resolver must be called with the local `db_pool`, "
        f"got: {ast.dump(arg)}"
    )

    # (2) called AFTER `db_pool = DbPool(...)` -- an unbound/unopened pool handed
    # to the resolver would be a construction-order bug.
    db_pool_lineno = _find_assignment_lineno(fn, "db_pool")
    assert call.lineno > db_pool_lineno, (
        "register_task_name_resolver(db_pool) must run AFTER `db_pool` is bound. "
        f"call at line {call.lineno}, db_pool assigned at line {db_pool_lineno}."
    )

    # (3) unconditional -- a bare, top-level statement in the function body,
    # not nested inside an `if`/`try` that could skip it silently on some
    # branch (a missing resolver degrades to a permanent tombstone, never an
    # exception, so a silently-skipped call would ship undetected by anything
    # except this guard).
    is_top_level = any(
        isinstance(stmt, ast.Expr) and stmt.value is call for stmt in fn.body
    )
    assert is_top_level, (
        "register_task_name_resolver(db_pool) must be a plain, unconditional "
        "top-level call in _phase_gateway, not nested inside an if/try/etc."
    )


def test_phase_gateway_wires_curated_memory_db_pool_after_db_pool_exists() -> None:
    """``set_curated_memory_db_pool(db_pool)`` (Story 2.8) is wired into
    ``_phase_gateway``, unconditionally, after ``db_pool`` is bound.

    THE BUG THIS GUARDS AGAINST: if this call is silently removed, reordered
    above the ``db_pool`` assignment, or wrapped in a conditional, every
    ``CuratedMemory`` write in production silently stops being journaled --
    ``memory/curated.py``'s ``_DB_POOL`` stays ``None``, and
    ``record_md_memory_write`` is a documented, never-raising no-op on that
    condition, so nothing else would ever catch the regression. Same AST-guard
    rationale as ``test_phase_gateway_registers_the_task_name_resolver_after_db_pool_exists``
    above -- see the module docstring.
    """
    fn = _phase_gateway_ast()

    call = _find_call_by_name(fn, "set_curated_memory_db_pool")

    assert len(call.args) == 1, "set_curated_memory_db_pool must take exactly one argument"
    arg = call.args[0]
    assert isinstance(arg, ast.Name) and arg.id == "db_pool", (
        f"set_curated_memory_db_pool must be called with the local `db_pool`, "
        f"got: {ast.dump(arg)}"
    )

    db_pool_lineno = _find_assignment_lineno(fn, "db_pool")
    assert call.lineno > db_pool_lineno, (
        "set_curated_memory_db_pool(db_pool) must run AFTER `db_pool` is bound. "
        f"call at line {call.lineno}, db_pool assigned at line {db_pool_lineno}."
    )

    is_top_level = any(
        isinstance(stmt, ast.Expr) and stmt.value is call for stmt in fn.body
    )
    assert is_top_level, (
        "set_curated_memory_db_pool(db_pool) must be a plain, unconditional "
        "top-level call in _phase_gateway, not nested inside an if/try/etc."
    )


def test_phase_gateway_wires_needs_you_db_pool_after_db_pool_exists() -> None:
    """``set_needs_you_db_pool(db_pool)`` (Story 3.1, AD-28) is wired into
    ``_phase_gateway``, unconditionally, after ``db_pool`` is bound.

    THE BUG THIS GUARDS AGAINST: if this call is silently removed, reordered
    above the ``db_pool`` assignment, or wrapped in a conditional,
    ``needs_you.py``'s retention-hold checker (registered into
    ``get_retention_hold_registry()`` at import time) stays wired to no pool
    forever -- it always returns ``frozenset()``, and ``journal_prune`` would
    then delete an unresolved incident's opening event exactly like an
    unheld row, with nothing else ever catching the regression (the checker
    itself never raises on an unwired pool -- see ``needs_you.py``'s own
    docstring). Same AST-guard rationale as the two tests above.
    """
    fn = _phase_gateway_ast()

    call = _find_call_by_name(fn, "set_needs_you_db_pool")

    assert len(call.args) == 1, "set_needs_you_db_pool must take exactly one argument"
    arg = call.args[0]
    assert isinstance(arg, ast.Name) and arg.id == "db_pool", (
        f"set_needs_you_db_pool must be called with the local `db_pool`, "
        f"got: {ast.dump(arg)}"
    )

    db_pool_lineno = _find_assignment_lineno(fn, "db_pool")
    assert call.lineno > db_pool_lineno, (
        "set_needs_you_db_pool(db_pool) must run AFTER `db_pool` is bound. "
        f"call at line {call.lineno}, db_pool assigned at line {db_pool_lineno}."
    )

    is_top_level = any(
        isinstance(stmt, ast.Expr) and stmt.value is call for stmt in fn.body
    )
    assert is_top_level, (
        "set_needs_you_db_pool(db_pool) must be a plain, unconditional "
        "top-level call in _phase_gateway, not nested inside an if/try/etc."
    )


def test_phase_gateway_calls_supervise_core_with_db_pool() -> None:
    """Story 3.1 (AD-28), review-pass amendment -- the one real
    ``_supervise_core(...)`` call site inside ``_phase_gateway`` passes
    ``db_pool=db_pool``, so a real Hello-mismatch stand-down can durably
    journal itself.

    THE BUG THIS GUARDS AGAINST: before this test existed, only the
    PARAMETER's existence on ``_supervise_core``'s signature and its
    behavior when explicitly passed were tested
    (``test_hello_mismatch_supervision.py``) -- nothing guarded that the
    REAL production call site actually supplies it, unlike every sibling
    parameter threaded into the same call (``stall_probe``, ``link_secret``,
    ``gateway_link``, etc.). A silently dropped/reordered/conditional
    ``db_pool=`` keyword there would make every real stand-down permanently
    skip journaling with nothing in the suite catching it -- exactly the
    kind of gap ``_supervise_core`` is called through ``asyncio.create_task``,
    not as a bare top-level statement, so this does NOT assert "top-level"
    like the two tests above; it asserts the keyword is present, correctly
    valued, and ordered after ``db_pool`` exists.
    """
    fn = _phase_gateway_ast()

    call = _find_call_by_name(fn, "_supervise_core")

    kwarg = next((kw for kw in call.keywords if kw.arg == "db_pool"), None)
    assert kwarg is not None, (
        "_supervise_core must be called with a db_pool= keyword argument"
    )
    assert isinstance(kwarg.value, ast.Name) and kwarg.value.id == "db_pool", (
        f"_supervise_core's db_pool= must be the local `db_pool`, got: "
        f"{ast.dump(kwarg.value)}"
    )

    db_pool_lineno = _find_assignment_lineno(fn, "db_pool")
    assert call.lineno > db_pool_lineno, (
        "_supervise_core(..., db_pool=db_pool) must run AFTER `db_pool` is "
        f"bound. call at line {call.lineno}, db_pool assigned at line "
        f"{db_pool_lineno}."
    )
