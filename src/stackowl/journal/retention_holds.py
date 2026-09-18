"""``RetentionHoldRegistry`` -- cursors the journal_prune job must never
delete (AD-6: "It never deletes an event referenced by an unresolved
Needs-you item").

PLACEMENT: inside ``journal/`` itself, alongside ``records.py`` and
``registry.py`` -- retention is explicitly named in AD-7's own package
inventory ("record-reader registry, narrator ... retention. Every subsystem
may import it. It imports nothing from ``bridge/``, ``voice/`` or any
subsystem"). A hold SOURCE (e.g. Epic 3's Needs-you items) registers a
checker; ``journal_prune`` calls :meth:`held_cursors` once per pass and
excludes the union from every DELETE.

SHAPE: mirrors ``records.py::RecordReaderRegistry`` exactly -- a module-level
dict guarded by a ``threading.RLock``, ``register_hold_source`` refuses a
duplicate name rather than silently replacing it, and
``reset_for_tests()``/a module-level ``reset_retention_holds_for_tests()``
give tests the same per-test-reset escape hatch every other journal registry
already has.

WHY THIS SHIPS NOW WITH ZERO CALLERS: epics.md's own AC text for this story
requires "it never deletes an event held by a registered retention hold" as
a PRESENT-TENSE behavior, not a deferred one -- Epic 3 (Needs-you items, the
first real hold source) does not exist yet. Story 2.10's
``RecordReaderRegistry`` already shipped this exact shape before ``bridge/``
existed (DW-29); this registry is provably correct and currently vacuous the
same way.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Awaitable, Callable

from stackowl.infra.observability import log

#: A hold source reports the cursors it currently needs kept, synchronously
#: or asynchronously -- ``journal_prune`` awaits/calls whichever shape a
#: registered checker returns.
HoldChecker = Callable[[], "frozenset[int] | Awaitable[frozenset[int]]"]


class RetentionHoldRegistry:
    """Process-wide registry of named retention-hold sources."""

    def __init__(self) -> None:
        self._sources: dict[str, HoldChecker] = {}
        self._lock = threading.RLock()

    def register_hold_source(self, name: str, checker: HoldChecker) -> None:
        """Register the one checker for ``name``. Raises ``ValueError`` on a
        duplicate registration -- mirrors
        ``records.py::RecordReaderRegistry.register``'s exact shape: one
        checker per name, never silently replaced."""
        # 1. ENTRY
        log.journal.debug(
            "[journal] RetentionHoldRegistry.register_hold_source: entry",
            extra={"_fields": {"name": name}},
        )
        with self._lock:
            # 2. DECISION -- refuse a duplicate registration for this name.
            if name in self._sources:
                log.journal.error(
                    "[journal] RetentionHoldRegistry.register_hold_source: "
                    "refused -- already registered",
                    extra={"_fields": {"name": name}},
                )
                raise ValueError(
                    f"retention hold source {name!r} is already registered "
                    "-- one checker per name"
                )
            # 3. STEP -- register it.
            self._sources[name] = checker
        # 4. EXIT
        log.journal.info(
            "[journal] RetentionHoldRegistry.register_hold_source: exit -- "
            "registered",
            extra={"_fields": {"name": name}},
        )

    async def held_cursors(self) -> frozenset[int]:
        """Union of every registered source's currently-held cursors.

        Awaits an async checker, calls a sync one directly -- computed once
        per ``journal_prune`` pass. A source that has nothing held returns an
        empty ``frozenset()``; there are no wired sources yet (see module
        docstring), so this returns ``frozenset()`` on a fresh registry.
        """
        with self._lock:
            checkers = list(self._sources.values())
        held: set[int] = set()
        for checker in checkers:
            result = checker()
            if inspect.isawaitable(result):
                result = await result
            held.update(result)
        return frozenset(held)

    def reset_for_tests(self) -> None:
        """Clear every registered hold source. Test-only -- mirrors
        ``records.py::RecordReaderRegistry.reset_for_tests``'s per-test reset
        convention."""
        with self._lock:
            self._sources.clear()


_retention_holds = RetentionHoldRegistry()


def get_retention_hold_registry() -> RetentionHoldRegistry:
    """The process-wide singleton every future hold source registers into
    and ``journal_prune`` reads from."""
    return _retention_holds


def reset_retention_holds_for_tests() -> None:
    """Module-level convenience mirroring ``records.py``'s own
    ``reset_record_readers_for_tests`` free function."""
    _retention_holds.reset_for_tests()
