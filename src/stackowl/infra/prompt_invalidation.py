"""Why the next prompt rebuild was expected (D01.4).

Layering, and the reason this lives in ``infra`` rather than beside either user:
``sessions/prompt_store.py`` performs the invalidation, ``pipeline/cache_audit.py``
consumes the explanation, and ``sessions`` must not import ``pipeline``. Both may
import ``infra``. This is the same trick, for the same reason, that
``infra/prompt_metrics.py`` uses to get prompt identity from the pipeline to the
providers.

**Why a module-level map and not a ContextVar.** The two halves happen in
DIFFERENT turns: the invalidation runs while handling a slash command, the audit
runs during the *next* turn's cold build. A ContextVar is per-async-context and
would be empty by then — it is the right tool for ``prompt_metrics`` (one turn)
and the wrong one here.

**What this is for.** ``D01.2``'s audit warns whenever a system-prompt part moves
between cold builds, which is exactly what it should do for an invalidator nobody
asked for. Once ``D01.4`` lets an edit apply immediately, every *deliberate* edit
produces the same signal — a warning about a change the user just requested. An
audit that cries wolf on the most common cause of a rebuild is an audit people
learn to ignore, which is the failure ``D01.2``'s validate stage caught from the
other direction.

So an explanation is recorded here and **consumed once**. One invalidation
explains one rebuild; a later unexplained change warns again.
"""

from __future__ import annotations

from collections import OrderedDict

from stackowl.infra.observability import log

# Bound on outstanding explanations. An explanation that is never consumed means
# the rebuild it predicted did not happen (the lane went idle, the process
# restarted), and holding it forever would eventually silence a genuine warning
# on some unrelated future rebuild. FIFO-evicting the oldest fails toward
# WARNING, which is the safe direction for an audit.
_MAX_PENDING = 256

_pending: OrderedDict[str, str] = OrderedDict()


def note_expected_change(owl_name: str, *, cause: str) -> None:
    """Record that ``owl_name``'s next prompt rebuild was asked for.

    Called by whatever performed the invalidation. Never raises — an explanation
    that fails to record costs a spurious warning, never a turn.
    """
    if not owl_name:
        # A change with no owl cannot be attributed, so it cannot be explained
        # either. Staying silent here means the audit still warns, which is the
        # correct direction to fail.
        return
    _pending[owl_name] = cause
    _pending.move_to_end(owl_name)
    while len(_pending) > _MAX_PENDING:
        _pending.popitem(last=False)
    log.gateway.debug(
        "[prompt] invalidation: expected change noted",
        extra={"_fields": {"owl": owl_name, "cause": cause}},
    )


def take_expected_change(owl_name: str) -> str | None:
    """The pending explanation for ``owl_name``, removing it. ``None`` if none.

    Consumed rather than read so a single edit cannot blind the audit for the
    rest of the process's life.
    """
    if not owl_name:
        return None
    return _pending.pop(owl_name, None)


def reset_expected_changes() -> None:
    """Forget every pending explanation. For tests — never called in production."""
    _pending.clear()
