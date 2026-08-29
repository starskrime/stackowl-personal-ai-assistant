"""One inbound message starts ONE pipeline.

MEASURED, 2026-08-29. Across the retained window 111 telegram turns reached the
pipeline and FOUR were dispatched twice within a second, always the same shape —
one run on the LANE and one on the RAW handle::

    18:13:43.084  run: entry  session_key='72055773'
    18:13:45.191  run: entry  session_key='owl:secretary:telegram:dm:72055773'

Three of the four landed within two minutes of a gateway boot, which is when the
dying and the fresh process can both briefly hold a live receive loop. The turn
then runs twice — two triages, two assembles, two tool loops (``owls_list`` was
called twice for one question) — and two answers race for one stream.

WHY THIS LIVES IN ITS OWN MODULE. ``_dispatch_turn`` is a closure inside
``_phase_gateway`` that no test can reach; five suites hand-copy its body and
orchestrator.py's own comment says to keep them in sync. A rule written inline
there is a rule nothing can pin. This is the smallest thing that is both callable
from the closure and testable on its own.
"""

from __future__ import annotations

from typing import Any

from stackowl.infra.observability import log


def already_dispatching(turn_registry: Any, trace_id: str) -> bool:
    """True when a LIVE turn is already registered for ``trace_id``.

    ``TurnRegistry`` is keyed on the trace id (``register(msg.trace_id, ...)``)
    and holds only turns still in flight — it deregisters on completion. So a hit
    here means a genuinely CONCURRENT duplicate, not a later re-run.

    THAT DISTINCTION IS THE WHOLE GUARD. Recovery re-drives deliberately reuse the
    original trace id, and they run minutes later when the first turn has long
    since deregistered. A naive "have I seen this trace id" set would strand every
    recovered turn — a far worse bug than the duplicate it prevents.

    FAILS OPEN. No registry, or a registry that raises, lets the turn through: a
    duplicate-suppressor that cannot read its own state must not become an outage.
    """
    if not trace_id or turn_registry is None:
        return False
    try:
        live = turn_registry.get(trace_id)
    except Exception as exc:  # a registry that cannot answer must not block a turn
        log.gateway.warning(
            "[gateway] duplicate check failed — dispatching anyway",
            exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
        )
        return False
    if live is None:
        return False
    log.gateway.warning(
        "[gateway] DUPLICATE dispatch refused — this message is already running",
        extra={"_fields": {
            "trace_id": trace_id,
            "running_session_key": getattr(live, "session_key", None),
        }},
    )
    return True
