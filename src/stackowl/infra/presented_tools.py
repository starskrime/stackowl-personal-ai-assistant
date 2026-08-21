"""PresentedToolStore — session-scoped memo of the fitted tool schemas (D05.2).

Why this exists, and it is NOT the reason you would guess. Ordering the presented
set by measured per-owl usage instead of the turn's request text makes *which*
tools rank highest stable — but it does not make the array stable, because the
budget moves too::

    execute.py    _fixed_cost = est(system_prompt) + est(EVERY history message)
    context_budget  tool_budget_tokens = window - fixed_cost

History grows every turn, so the tool-token budget shrinks every turn and
``fit_items`` admits fewer candidates — under a perfectly fixed ordering. That is
what made D01.3's measured tool COUNT oscillate (5→4→5→5→5 on one lane); an
ordering defect can reshuffle a fixed-size array but cannot change its length.

The fix is to take the measurement ONCE, at the session's first turn, and reuse
it — not to remove history from the measurement. On turn 40 the history IS the
remaining room, so a history-free budget would be stable and wrong exactly when
the window is tightest, which is the failure the budgeter exists to prevent.

Placed in ``infra/`` beside ``hydrated_tools.py``, which it mirrors: a plain
in-memory, per-process, session-keyed store rather than a ContextVar, because it
must survive ACROSS turns of one session rather than within one. Best-effort —
losing it (restart, recycle) reverts to today's rebuild-every-turn behaviour,
never a correctness issue.

WHAT IS IN THE KEY, and why each entry is load-bearing:

* ``session_key`` — the stability horizon. A new session must recompute, or a
  learned demotion could never take effect.
* ``owl`` — two owls on one lane have different profiles and different learned
  cores.
* ``protocol`` and ``window`` — ``build_tool_schemas`` is deliberately re-invoked
  per escalation tier, and the tiers "can speak different wire protocols and have
  different context windows". Keyed on the session alone, an Anthropic-shaped
  array would be served to an OpenAI-protocol tier.
* ``hydrated`` — FX-07 promotes a ``tool_search`` hit into the NEXT turn's
  presented schema. Without it in the key the memo swallows that promotion
  silently: the model searches, finds the tool, and never receives it. So a
  discovery invalidates and costs one rebuild. That is the intended trade — the
  hydrated set is monotonic within a session and grows only on an explicit
  search, so invalidations are bounded by discoveries, not by turns, and the
  prefix changes because the toolset genuinely changed.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

from stackowl.infra.observability import log

__all__ = ["budget_basis", "clear", "clear_owl", "get", "make_key", "put"]

#: Bounded LRU across all sessions. Each entry is a list of schema dicts, so the
#: cap is on memory, not correctness — an eviction just rebuilds next turn.
_MAX_ENTRIES = 256

_lock = threading.Lock()
#: Key: (session_key, owl, provider, protocol, window, hydrated). ``provider`` was
#: added 2026-08-21 — without it, two DIFFERENT backends speaking one protocol shared
#: an entry, and the array they shared is BUDGETED to a window, so the weaker backend
#: was served a set fitted to the stronger one's. See make_key().
_MemoKey = tuple[str, str, str, str, int, tuple[str, ...]]
_memo: OrderedDict[_MemoKey, list[dict[str, Any]]] = OrderedDict()

#: D05.4 — the SESSION'S budget basis, which is a different thing from the memo and
#: has a different lifetime. The memo key without ``hydrated``: discovering a tool
#: adds one to the array, it does not change how much room the history leaves.
_BasisKey = tuple[str, str, str, str, int]
_basis: OrderedDict[_BasisKey, int] = OrderedDict()


def _on_capability_change(capability: str) -> None:
    """Drop every memoized array when a capability's verdict changes (D05.3).

    Without this the availability gate would be evaluated once per session and a
    newly-configured capability would not appear until rollover — "I added the
    API key and nothing happened".

    Deliberately clears EVERYTHING rather than only the affected entries: the
    memo key does not record which capabilities a given array depended on, and
    inventing that bookkeeping to save a rebuild that happens on a human-scale
    event would be the expensive kind of clever. Capability flips are rare; a
    turn-frequency invalidation this is not.

    THAT LAST SENTENCE IS MEASURED FALSE (D05.4, 2026-08-21): 950 wipes all-time,
    62 in a single day, 100% of them the SAME capability — `browser` — from a
    ~3-second subprocess recycle. The scope is still not narrowed here, because
    after D05.4 a needless rebuild costs CPU and no longer costs the array: the
    BASIS survives this wipe, so the rebuild reproduces what the model already
    had. Fixing the trigger (a transient bounce is not a capability change) is a
    cost defect and is tracked separately.
    """
    log.infra.info(
        "[presented_tools] capability changed — dropping every memoized tool array",
        extra={"_fields": {"capability": capability or "(all)", "dropped": len(_memo)}},
    )
    # The MEMO only. Dropping the basis here is what made this wipe a capability
    # amputation: the rebuild would re-measure a history 40 messages longer and
    # fit fewer tools. Measured before the fix, real registry, 16k window: 66
    # tools before the wipe, 53 after — `objective`, `process`, `send_message`,
    # `run_tests` and nine more, gone mid-conversation because a subprocess
    # bounced.
    _drop_memo(None)


def _subscribe_once() -> None:
    """Wire the capability→memo invalidation exactly once, on first import."""
    from stackowl.infra import capabilities

    capabilities.subscribe_to_changes(_on_capability_change)


def make_key(
    *,
    session_key: str,
    owl: str,
    provider: str,
    protocol: str,
    window: int,
    hydrated: set[str] | None,
) -> _MemoKey:
    """Build the memo key. ``hydrated`` is SORTED into a tuple, not left a set —
    a set has no stable iteration order to hash a key on, and two identical
    hydrated sets must produce the same key or the memo never hits.

    ``provider`` IS PART OF THE KEY, since 2026-08-21. Without it two different
    backends speaking one protocol collided inside a single session, and the first
    to build an array handed it to the second. That is not a cache inefficiency:
    the array is budgeted to a window (see ``execute.py``'s ``budget=`` argument,
    whose stated purpose is that "a weak/small-window model is not drowned in tool
    schemas"), and ``window`` could not separate them either — it is stamped once by
    assemble and never re-stamped per tier, while the escalation ladder rebuilds
    schemas for EACH tier's provider.

    Measured: 1,530 real traces used more than one provider and 254 more than one
    model, spanning a 2b to a 122b, all on ``protocol: openai``. Dormant today
    (three of four backends are disabled, last multi-model trace 2026-07-20) and it
    re-arms the moment a second backend is enabled.
    """
    return (
        session_key, owl, provider, protocol, window, tuple(sorted(hydrated or ()))
    )


def get(key: _MemoKey) -> list[dict[str, Any]] | None:
    """Return the memoized schemas for this key, or None."""
    with _lock:
        hit = _memo.get(key)
        if hit is None:
            return None
        _memo.move_to_end(key)
        # Copy on the way out. Callers mutate the returned list (the depth>0
        # spawn-tool exclusion filters it), and handing out the stored list would
        # let one delegated child's exclusion silently persist onto every later
        # turn of the parent's session.
        return list(hit)


def put(key: _MemoKey, schemas: list[dict[str, Any]]) -> None:
    """Memoize schemas for this key, evicting the least recently used."""
    with _lock:
        _memo[key] = list(schemas)
        _memo.move_to_end(key)
        while len(_memo) > _MAX_ENTRIES:
            _memo.popitem(last=False)


def clear_owl(owl: str) -> None:
    """Drop every memo entry for one owl, across all its sessions.

    Called wherever the frozen prompt is invalidated for an owl edit (D01.4).
    The two must move together: an edit that changed ``capability_profile`` or
    ``tools`` changes which tools should be presented, and the memo key holds the
    owl's NAME, not a fingerprint of its manifest — so without this a
    self-extending owl would keep being handed its pre-edit toolset for the rest
    of the session, unable to use the capability it had just given itself.

    Same in-flight rule as the prompt: the current turn keeps what it started
    with; the next turn rebuilds.
    """
    with _lock:
        # Index 1 is the owl. `provider` was inserted at index 2 in 2026-08-21's key
        # change specifically so session_key and owl keep positions 0 and 1 and this
        # scan is unaffected — a positional read is the kind of thing that breaks in
        # silence, so it is pinned by test_clear_owl_still_finds_the_owl.
        for key in [k for k in _memo if k[1] == owl]:
            del _memo[key]
        # The basis too: an owl edit changes the SYSTEM PROMPT, which is half of
        # the fixed cost the array was fitted against. Keeping a stale basis
        # across an edit would fit the new prompt to the old measurement.
        for bkey in [k for k in _basis if k[1] == owl]:
            del _basis[bkey]


def _drop_memo(session_key: str | None) -> None:
    """Drop memo entries WITHOUT touching the budget basis.

    Separate from :func:`clear` because the two have different meanings. A
    capability flip says "this array may be stale" — rebuild it. It does not say
    "this conversation is over", which is the only thing that should re-measure
    how much room the history leaves. Conflating them is what let a 3-second
    browser recycle shrink a live agent's toolset.
    """
    with _lock:
        if session_key is None:
            _memo.clear()
            return
        for key in [k for k in _memo if k[0] == session_key]:
            del _memo[key]


def budget_basis(key: _MemoKey, measured: int) -> int:
    """The fixed-cost figure this session's array is fitted against (D05.4).

    Stamped from the FIRST tool turn and reused, so rebuilding the array later in
    the conversation reproduces it. Without this, the presented set is a function
    of the turn rather than of the conversation: history grows, ``fit_items``
    admits fewer candidates, and every memo miss quietly costs the agent tools it
    had a moment earlier.

    This does not change what a memo HIT serves — a hit already ignores the
    budget entirely. It makes the MISS agree with the hit, which is what turns the
    memo back into a cache. `to_provider_schema`'s own docstring names the gap it
    closes: the method cannot be "pure across turns it cannot see", so the caller
    has to hand it an input that does not move.

    Keyed without ``hydrated``: a tool_search discovery legitimately adds to the
    array, and re-measuring the room because of it would reintroduce the drift.

    Bounded at the same ``_MAX_ENTRIES`` as the memo, and by the same LRU, so the
    two evict together on a busy box. A basis evicted mid-conversation re-measures
    on the next rebuild — the one case where the array can still move — which is
    the same trade the memo already makes and is why the bound is on both or
    neither.
    """
    basis_key: _BasisKey = key[:5]
    with _lock:
        stamped = _basis.get(basis_key)
        if stamped is not None:
            _basis.move_to_end(basis_key)
            return stamped
        _basis[basis_key] = measured
        _basis.move_to_end(basis_key)
        while len(_basis) > _MAX_ENTRIES:
            _basis.popitem(last=False)
        return measured


def clear(session_key: str | None = None) -> None:
    """Drop one session's memo entries AND its budget basis, or all of them.

    The session-scoped form is what a rollover calls so the next incarnation
    picks up a freshly learned core — and a freshly measured basis, since a new
    incarnation starts from a fresh history.
    """
    _drop_memo(session_key)
    with _lock:
        if session_key is None:
            _basis.clear()
            return
        for bkey in [k for k in _basis if k[0] == session_key]:
            del _basis[bkey]


# Wire the capability→memo invalidation at import. Done here rather than in the
# orchestrator so it holds in tests and in any process that builds schemas, not
# only the one that happens to run startup wiring — the failure it prevents
# ("I added the API key and nothing happened") is silent, and a wiring step that
# can be forgotten is how it would come back.
_subscribe_once()
