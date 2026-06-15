"""F038 — tool.heuristic_match was emitted to ZERO production subscribers.

The per-tool-call ``match_and_emit`` did an async ``find_for_tool`` DB lookup then
``EventBus.emit("tool.heuristic_match", ...)`` — but the only subscribers were test
files. No production consumer existed, so the emit was dead and the DB lookup was
pure hot-path latency feeding nobody. Per the C8 spec (option b) we DEMOTE: drop the
per-call emit AND the per-call DB lookup from execute, log honestly instead.

These tests assert the OUTCOMES: the execute hot path no longer wires the bus into
the matcher, and no production EventBus subscriber exists for the dead event.
"""

from __future__ import annotations

import inspect

from stackowl.pipeline.steps import execute as execute_mod


def _execute_src() -> str:
    return inspect.getsource(execute_mod)


def test_execute_no_longer_emits_the_dead_heuristic_event_via_bus() -> None:
    """The execute step must NOT call match_and_emit (the bus emitter) — the dead
    event had no production subscriber. It logs the heuristic instead."""
    src = _execute_src()
    assert "match_and_emit" not in src, (
        "execute must not emit the dead tool.heuristic_match event on the bus"
    )


def test_execute_does_not_do_a_per_call_heuristic_db_lookup() -> None:
    """The per-call find_for_tool lookup fed nobody — it is removed from the hot
    path (a deliberate latency win, not a behavior loss)."""
    src = _execute_src()
    assert "find_for_tool" not in src
    # The honest demoted log lives here instead.
    assert "match_and_log" in src


def test_no_production_subscriber_to_dead_event() -> None:
    """No non-test module subscribes to ``tool.heuristic_match`` — confirming the
    demote removed a dead event rather than a wired consumer."""
    import pathlib

    src_root = pathlib.Path(execute_mod.__file__).parents[2]  # .../stackowl
    offenders: list[str] = []
    for py in src_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if 'subscribe("tool.heuristic_match"' in text or "subscribe('tool.heuristic_match'" in text:
            offenders.append(str(py))
    assert offenders == [], f"unexpected production subscriber(s): {offenders}"


def test_demoted_matcher_logs_without_bus_or_db(caplog) -> None:  # type: ignore[no-untyped-def]
    """``match_and_log`` is a pure, no-IO honest log of the tool outcome — no
    EventBus, no DB lookup, never raises."""
    import logging

    from stackowl.learning.heuristic_matcher import match_and_log
    from stackowl.tools.base import ToolResult

    failed = ToolResult(success=False, output="", error="ValueError: x", duration_ms=1.0)
    with caplog.at_level(logging.INFO):
        match_and_log(tool_name="web_fetch", tool_result=failed)  # no store, no bus
    # The function exists, accepts only (tool_name, tool_result), and does not raise.
    sig = inspect.signature(match_and_log)
    assert set(sig.parameters) == {"tool_name", "tool_result"}
