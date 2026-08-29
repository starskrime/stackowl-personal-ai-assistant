"""One inbound message starts ONE pipeline.

BAKIR, 2026-08-29: "Platform failing on single ask."

MEASURED. Across the whole retained window, 111 telegram turns reached the
pipeline and FOUR were dispatched twice within a second of each other — always the
same shape, one run on the LANE and one on the RAW handle::

    18:13:43.084  run: entry  session_key='72055773'
    18:13:45.191  run: entry  session_key='owl:secretary:telegram:dm:72055773'

Three of the four landed within two minutes of a gateway boot, which is when both
the dying and the fresh process can briefly hold a live receive loop. The turn then
runs twice: two triages, two assembles, two tool loops — `owls_list` was called
twice for one question — and two answers race for one stream.

WHY A TIME-BOUND IS NOT NEEDED. `TurnRegistry` holds only LIVE turns, keyed on
``trace_id`` (``register(msg.trace_id, ...)``), and deregisters on completion. So
"a turn is already registered for this trace" means a genuinely CONCURRENT
duplicate. A recovery re-drive reuses the original trace id deliberately, but runs
minutes later when the first has long since deregistered — it must still be
allowed, and it is.

HONEST COVERAGE NOTE. ``_dispatch_turn`` is a closure inside ``_phase_gateway``
that no test can reach — five suites hand-copy its body, and orchestrator.py's own
comment says to keep them in sync. So the rule lives in this module-level function
instead of inline in the closure, which is the only way it can be tested at all.
"""

from __future__ import annotations

from typing import Any


class _Registry:
    def __init__(self, live: dict[str, Any] | None = None) -> None:
        self._live = live or {}

    def get(self, request_id: str) -> Any:
        return self._live.get(request_id)


def test_a_second_dispatch_of_a_LIVE_trace_is_refused() -> None:
    """The bug: the same message dispatched twice, one second apart."""
    from stackowl.gateway.duplicate_dispatch import already_dispatching

    reg = _Registry({"trace-abc": object()})
    assert already_dispatching(reg, "trace-abc") is True


def test_a_trace_with_no_LIVE_turn_is_allowed() -> None:
    """A recovery re-drive reuses the trace id and MUST still run.

    It happens after the first turn completed and deregistered, so the registry is
    empty for that id. Blocking it would strand recovered work — a worse bug than
    the one being fixed.
    """
    from stackowl.gateway.duplicate_dispatch import already_dispatching

    assert already_dispatching(_Registry(), "trace-abc") is False


def test_a_missing_registry_never_blocks_a_turn() -> None:
    """Fail OPEN.

    A guard that cannot read the registry must let the turn through. Refusing on
    an unreadable registry would turn a duplicate-suppressor into a total outage.
    """
    from stackowl.gateway.duplicate_dispatch import already_dispatching

    class _Broken:
        def get(self, request_id: str) -> Any:
            raise RuntimeError("registry unavailable")

    assert already_dispatching(None, "trace-abc") is False
    assert already_dispatching(_Broken(), "trace-abc") is False


def test_an_empty_trace_id_never_blocks() -> None:
    """Two turns with no trace id are not 'the same turn'."""
    from stackowl.gateway.duplicate_dispatch import already_dispatching

    assert already_dispatching(_Registry({"": object()}), "") is False
