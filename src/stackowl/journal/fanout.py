"""``journal/fanout.py`` -- the pure, DB-injected read/notify primitive Spec
2.5 builds split-mode TUI progress on.

Nothing here talks to ``ipc/`` or any subsystem: this module owns exactly two
things a fan-out reader needs, both DB-injected through the structural
:class:`RowFetcher` protocol so this package never imports ``db.pool``
(``journal/__init__.py``'s package-placement rule) --

* :func:`read_since` / :func:`current_max_cursor` -- one bounded, ordered
  read of ``journal_events`` past a cursor, and the table's current high
  watermark at boot. Every read here is a single ``fetch_all`` -- never a
  ``transaction()``, never held open across more than one query (AD-38).
* :func:`notify_committed` / :func:`wait_for_commit` -- a process-local wake
  signal (module-level ``asyncio.Event``, mirroring ``journal/write_gate.py``'s
  module-state shape) so a push loop is woken only by a real commit, never a
  timer. Core's push loop and (Story 2.6+) gateway-side emitters both call
  ``notify_committed()`` after their own commit; nothing here decides WHO
  calls it or WHEN a commit happens -- that stays the caller's business
  (``pipeline/durable/store.py`` today).

Why a set/wait/clear ``asyncio.Event`` never loses a row even under a race
with the caller: ``notify_committed()`` is a bare, non-``await``-ing call
placed by its callers strictly AFTER their transaction's commit returns, so
by the time it runs the row is already visible to any ``fetch_all``. A reader
that wakes from ``wait_for_commit()`` always re-queries the table directly
(never trusts the event to carry data) — so a notification racing the
reader's own ``clear()`` can only ever cost one extra, harmless wakeup on a
row the very next bounded read already picks up; it can never lose one.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from stackowl.infra.observability import log

#: The bounded read every fan-out reader (core's push loop, the gateway's
#: catch-up) uses by default -- one page, never an unbounded scan (AD-38).
_DEFAULT_LIMIT = 200

_SELECT_SINCE_SQL = (
    "SELECT cursor, event_id, type, schema_version, occurred_at, actor_kind, "
    "actor_id, device_id, target_kind, target_id, outcome, attention, "
    "intensity, record_ref, attrs, trace_id, duration_ms "
    "FROM journal_events WHERE cursor > ? ORDER BY cursor LIMIT ?"
)
_SELECT_MAX_CURSOR_SQL = "SELECT COALESCE(MAX(cursor), 0) AS max_cursor FROM journal_events"


class RowFetcher(Protocol):
    """The exact structural shape of ``DbPool.fetch_all`` -- matched
    structurally so this module never imports ``db.pool`` (package-placement
    rule, ``journal/__init__.py``'s module docstring)."""

    async def fetch_all(
        self, sql: str, params: Sequence[Any] = ()
    ) -> list[dict[str, object]]: ...  # noqa: D102


@dataclass(frozen=True)
class JournalRow:
    """One decoded ``journal_events`` row -- the envelope fan-out delivers.

    Mirrors ``journal/models.py``'s ``JournalEvent`` envelope fields plus the
    table's own ``cursor``/``event_id`` (which ``JournalEvent`` never carries,
    since ``recorder.record`` fills those at insert time). ``attrs`` and
    ``record_ref`` are already JSON-decoded here -- callers never re-parse.
    """

    cursor: int
    event_id: str
    type: str
    schema_version: int
    occurred_at: str
    actor_kind: str
    actor_id: str
    device_id: str | None
    target_kind: str
    target_id: str
    outcome: str
    attention: str | None
    intensity: str | None
    record_ref: dict[str, object] | None
    attrs: dict[str, object]
    trace_id: str | None
    duration_ms: int | None


def _str_or_none(value: object) -> str | None:
    """``str(value)`` unless ``value`` is already ``None`` -- several
    envelope columns are nullable TEXT and must round-trip ``NULL`` as
    ``None``, not the string ``"None"``."""
    return None if value is None else str(value)


def _decode_row(row: Mapping[str, Any]) -> JournalRow:
    attrs_raw = row["attrs"]
    attrs: dict[str, object] = json.loads(attrs_raw) if attrs_raw else {}
    record_ref_raw = row["record_ref"]
    record_ref: dict[str, object] | None = (
        json.loads(record_ref_raw) if record_ref_raw else None
    )
    return JournalRow(
        cursor=int(row["cursor"]),
        event_id=str(row["event_id"]),
        type=str(row["type"]),
        schema_version=int(row["schema_version"]),
        occurred_at=str(row["occurred_at"]),
        actor_kind=str(row["actor_kind"]),
        actor_id=str(row["actor_id"]),
        device_id=_str_or_none(row["device_id"]),
        target_kind=str(row["target_kind"]),
        target_id=str(row["target_id"]),
        outcome=str(row["outcome"]),
        attention=_str_or_none(row["attention"]),
        intensity=_str_or_none(row["intensity"]),
        record_ref=record_ref,
        attrs=attrs,
        trace_id=_str_or_none(row["trace_id"]),
        duration_ms=int(row["duration_ms"]) if row["duration_ms"] is not None else None,
    )


async def read_since(
    fetcher: RowFetcher, cursor: int, limit: int = _DEFAULT_LIMIT
) -> list[JournalRow]:
    """Every row committed past ``cursor``, in cursor order, bounded to
    ``limit``. One ``fetch_all`` -- never a held-open transaction (AD-38).

    A cursor "hole" (a row rolled back, or already pruned) is simply absent
    from the result -- ``WHERE cursor > ?`` never blocks on a gap, it just
    returns whatever committed rows exist above it (AD-9).
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] fanout.read_since: entry",
        extra={"_fields": {"cursor": cursor, "limit": limit}},
    )
    rows = await fetcher.fetch_all(_SELECT_SINCE_SQL, (cursor, limit))
    decoded = [_decode_row(r) for r in rows]
    # 4. EXIT
    log.journal.debug(
        "[journal] fanout.read_since: exit",
        extra={"_fields": {"cursor": cursor, "row_count": len(decoded)}},
    )
    return decoded


async def current_max_cursor(fetcher: RowFetcher) -> int:
    """The table's current high watermark, or 0 on an empty table.

    Both the core push loop and the gateway's own watermark initialize from
    this at boot (own process start) -- neither ever floods a fresh
    connection with full history (Boundaries & Constraints).
    """
    # 1. ENTRY
    log.journal.debug("[journal] fanout.current_max_cursor: entry")
    rows = await fetcher.fetch_all(_SELECT_MAX_CURSOR_SQL, ())
    # SQLite's COALESCE(MAX(cursor), 0) always yields an integer; `cast` (not
    # `int(...)`) because `RowFetcher`'s protocol return type is intentionally
    # `object`-valued (structural match to `DbPool.fetch_all`) and `int()` has
    # no overload accepting a bare `object`.
    cursor = cast(int, rows[0]["max_cursor"]) if rows else 0
    # 4. EXIT
    log.journal.debug(
        "[journal] fanout.current_max_cursor: exit",
        extra={"_fields": {"cursor": cursor}},
    )
    return cursor


# --- process-local commit signal (never a timer) ---------------------------
#
# Module-level state, mirroring ``journal/write_gate.py``'s shape -- a single
# process-wide signal every writer's committer wakes, and every reader's push
# loop waits on. See the module docstring for why a set/wait/clear
# ``asyncio.Event`` cannot lose a row even under a same-process race.

_commit_event = asyncio.Event()


def notify_committed() -> None:
    """Wake any waiter -- called by a writer immediately AFTER its own
    transaction commits (never before, and never inside the transaction
    itself). Idempotent: calling it with nobody waiting just leaves the
    event set, so the next :func:`wait_for_commit` returns immediately."""
    # 1. ENTRY / 3. STEP -- one flag set, no I/O.
    log.journal.debug("[journal] fanout.notify_committed: entry")
    _commit_event.set()
    # 4. EXIT
    log.journal.debug("[journal] fanout.notify_committed: exit")


async def wait_for_commit(timeout: float | None = None) -> None:
    """Block until the next :func:`notify_committed`, or ``timeout`` seconds
    elapse (never raises on timeout -- it just returns, so a caller with a
    timeout treats this as "check again" rather than an error). ``None``
    (the default) waits with no ceiling -- the normal shape for a
    long-lived push loop, which is never a poll: it wakes ONLY on a real
    commit."""
    # 1. ENTRY
    log.journal.debug(
        "[journal] fanout.wait_for_commit: entry",
        extra={"_fields": {"timeout": timeout}},
    )
    # 2. DECISION -- an unbounded wait (the normal long-lived-loop shape) vs a
    # timed one (a caller that wants to poll something else between checks).
    if timeout is None:
        await _commit_event.wait()
    else:
        try:
            await asyncio.wait_for(_commit_event.wait(), timeout=timeout)
        except TimeoutError:
            log.journal.debug("[journal] fanout.wait_for_commit: timed out")
            return
    # 3. STEP -- consume the signal so the NEXT wait blocks for a NEW commit.
    _commit_event.clear()
    # 4. EXIT
    log.journal.debug("[journal] fanout.wait_for_commit: exit -- woken")


def reset_for_tests() -> None:
    """Rebind the commit signal to a FRESH ``asyncio.Event``. Test-only --
    mirrors ``journal/write_gate.py``'s ``reset_for_tests()`` per-test reset
    convention; the event is otherwise process-lifetime.

    A plain ``.clear()`` is not enough here: ``asyncio.Event`` binds to
    whichever loop is running the first time it is touched, and a real
    process has exactly one loop for its whole lifetime, so this never
    matters in production. A test suite that hands each test its own event
    loop (pytest-asyncio's function-scoped default) is not so lucky --
    reusing one instance across tests raises ``RuntimeError: ... is bound to
    a different event loop`` the moment a second test's loop touches it, so
    this rebinds the NAME to a new ``Event()`` rather than merely clearing
    the old one's flag.
    """
    global _commit_event
    _commit_event = asyncio.Event()
