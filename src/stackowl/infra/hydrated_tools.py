"""HydratedToolStore — session-scoped memory of tools recently surfaced via
tool_search, so a discovered tool is promoted into the NEXT turn's presented
schema instead of the model re-discovering the same tool every turn (FX-07).

Lives in ``infra/`` (the base layer) — mirrors ``recovery_context.py``'s
placement so both ``tools/meta/tool_search.py`` (the write side) and
``pipeline/steps/execute.py`` (the read side) can import it without a
layering cycle. Unlike that module, this is deliberately NOT a ContextVar: it
must survive ACROSS turns of the same session, not just within one, so it's a
plain in-memory, per-process, session-keyed store. Bounded and best-effort —
losing it (restart, runtime recycle) just reverts to today's
re-search-every-turn behavior, never a correctness issue.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

__all__ = ["clear", "get", "record"]

#: Per-session cap so a long-lived session's hydrated set can't grow unbounded;
#: most-recently-surfaced names are kept, oldest evicted.
_MAX_PER_SESSION = 12

_lock = threading.Lock()
_by_session: dict[str, OrderedDict[str, None]] = {}


def record(session_id: str | None, names: list[str]) -> None:
    """Record tools surfaced this turn; most-recently-seen evicts oldest."""
    if not session_id or not names:
        return
    with _lock:
        bucket = _by_session.setdefault(session_id, OrderedDict())
        for name in names:
            bucket.pop(name, None)
            bucket[name] = None
        while len(bucket) > _MAX_PER_SESSION:
            bucket.popitem(last=False)


def get(session_id: str | None) -> set[str]:
    """Return the current hydrated-tool names for a session (empty if none)."""
    if not session_id:
        return set()
    with _lock:
        bucket = _by_session.get(session_id)
        return set(bucket) if bucket else set()


def clear(session_id: str) -> None:
    """Drop a session's hydrated set entirely (e.g. on session close)."""
    with _lock:
        _by_session.pop(session_id, None)
