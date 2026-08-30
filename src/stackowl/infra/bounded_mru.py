"""A bounded, MRU-evicting map — the shape four prompt-part snapshots share.

WHY THIS EXISTS. Four places freeze a mutable input per conversation so the
cached prompt prefix stops moving underneath itself: the curated `profile` block,
the rendered skills catalogue, the tool-usage ordering, and (ESC-67) the learned
`stable_context`. Each had hand-written the same `OrderedDict` + `move_to_end` +
`popitem(last=False)` dance with its own `_MAX_TRACKED = 64`. CLAUDE.md names
"two copies of one rule" as one of the shapes that accounts for nearly every real
defect here; DEBT-39 filed it at three, and ESC-67 would have made four.

WHAT IT DELIBERATELY DOES NOT DO — compute the value. An earlier draft took a
`compute` callback, which forced a choice between a sync and an async variant
(the usage snapshot awaits a store read; the catalogue renders synchronously) and
would have meant either two primitives or a coroutine-shaped API for callers that
have nothing to await. Splitting `peek` from `put` sidesteps that entirely: each
caller runs its own computation, sync or async, between the two calls.

It also leaves the EMPTY-VALUE POLICY to the caller, on purpose. The catalogue
must not cache an empty render (freezing "" would strip the catalogue from every
remaining turn); the usage snapshot must not cache empty scores (freezing {} would
pin a conversation to cold-start order after one transient store error); the
curated snapshot DOES store an empty dict, because that dict is then populated
per target in place. One policy could not have served all three, and guessing a
default here would have quietly changed two shipped behaviours.

PLACEMENT: `infra/` beside `clock`, `trace` and `observability` — it is a
dependency-free container with no knowledge of prompts, and putting it in any one
of the four calling packages would make the other three import across a boundary
for a data structure. The alternative considered was `memory/`, where the first
copy appeared; rejected because the tools and skills callers would then depend on
memory for a dict.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

#: Conversations retained before the oldest is dropped. One value, one place —
#: the four callers previously each declared their own 64.
DEFAULT_MAX_TRACKED = 64


class BoundedMRU[K, V]:
    """Least-recently-used map with a hard bound on entries.

    Eviction takes the LEAST recently used, which is the point: dropping the
    conversation currently running would re-read the frozen input mid-session and
    restore the exact bug these snapshots exist to remove.
    """

    def __init__(
        self,
        max_tracked: int = DEFAULT_MAX_TRACKED,
        *,
        on_evict: Callable[[K], None] | None = None,
    ) -> None:
        self._max_tracked = max_tracked
        self._on_evict = on_evict
        self._items: OrderedDict[K, V] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def peek(self, key: K) -> V | None:
        """Return the stored value and mark it most-recently-used, or None.

        The MRU touch happens on READ as well as write — a long conversation that
        keeps hitting its snapshot must not age out while it is still live.
        """
        if key not in self._items:
            return None
        self._items.move_to_end(key)
        return self._items[key]

    def put(self, key: K, value: V) -> V:
        """Store, mark most-recently-used, and evict down to the bound.

        Returns the value, so a caller can write ``return mru.put(k, computed)``.
        """
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._max_tracked:
            evicted, _ = self._items.popitem(last=False)
            if self._on_evict is not None:
                self._on_evict(evicted)
        return value

    def clear(self) -> None:
        """Drop everything. For tests and for a deliberate reset."""
        self._items.clear()
