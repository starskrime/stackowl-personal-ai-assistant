"""ParkedIntakes — the raw-IngressMessage park map with sweep-driven eviction.

STEER-3/F057: a queued :class:`PendingIntake` carries only
``request_id``/``original_input``/``target``, so to RE-DISPATCH it faithfully the
orchestrator parks the original raw :class:`IngressMessage` here, keyed by
``request_id``, and pops it when the queue entry is drained. The pre-existing leak:
entries were popped ONLY on a successful drain — if the drain task is GC'd, or a
session WEDGES (its ``_running`` slot stuck), the parked entry is never popped and
the map grows without bound (a slow leak of raw messages).

This class encapsulates that map (was a bare closure-local dict) and adds the
missing eviction seam: :meth:`evict` removes entries whose ``request_id`` — or a
``{request_id}-survivor-N`` key DERIVED from it by the completion seam — appears in
the set of request_ids the turn sweep reaped. The orchestrator wires
:meth:`evict` to :meth:`TurnRegistry.set_reaped_evictor` so the recurring F050
sweep that already reaps wedged turns ALSO reclaims their parked intakes — the
leak is closed at the SAME backstop that detects the wedge.

In-memory and bounded by the registry's per-session/global queue caps; never
raises. NOT thread-safe by design — every caller runs on the single gateway event
loop (the same discipline as the registry's own maps).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.gateway.scanner import IngressMessage

# Suffix the completion seam uses to park drained survivor steers
# (``{finished_request_id}-survivor-{i}``). Kept here so eviction can reclaim a
# survivor parked under a reaped PARENT request_id (the parent never drains those
# survivors itself once it is reaped).
_SURVIVOR_SUFFIX = "-survivor-"


class ParkedIntakes:
    """A request_id → raw IngressMessage park map with sweep-driven eviction."""

    def __init__(self) -> None:
        self._parked: dict[str, IngressMessage] = {}

    def put(self, request_id: str, message: IngressMessage) -> None:
        """Park ``message`` under ``request_id`` (overwrites a stale duplicate)."""
        self._parked[request_id] = message

    def get_and_pop(self, request_id: str) -> IngressMessage | None:
        """Pop the parked message for ``request_id`` (the normal drain path)."""
        return self._parked.pop(request_id, None)

    def __len__(self) -> int:
        return len(self._parked)

    def evict(self, reaped_request_ids: list[str]) -> int:
        """Evict parked entries for reaped turns (STEER-3/F057). Returns the count.

        Removes any parked key that EITHER equals a reaped ``request_id`` OR is a
        ``{reaped_rid}-survivor-N`` key derived from it (a survivor parked by the
        completion seam whose parent turn was reaped before it could drain). Pure
        bookkeeping — never raises; an empty/None-ish input is a no-op.
        """
        if not reaped_request_ids:
            return 0
        reaped = set(reaped_request_ids)
        to_remove: list[str] = []
        for key in self._parked:
            if key in reaped:
                to_remove.append(key)
                continue
            base, sep, _ = key.partition(_SURVIVOR_SUFFIX)
            if sep and base in reaped:
                to_remove.append(key)
        for key in to_remove:
            self._parked.pop(key, None)
        if to_remove:
            log.gateway.info(
                "[gateway] parked_intakes: evicted reaped entries (F057 leak guard)",
                extra={"_fields": {"evicted": len(to_remove), "remaining": len(self._parked)}},
            )
        return len(to_remove)
