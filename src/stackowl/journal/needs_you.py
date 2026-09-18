"""Durable Needs-you items -- open/resolve/query primitives, and the
``needs_you.opened``/``needs_you.resolved`` event types themselves
(Story 3.1, AD-28).

PLACEMENT: inside ``journal/`` itself -- AD-7 names "Needs-you items and
resolver" explicitly in ``journal/``'s own package inventory. This module
owns the ``needs_you`` table (migration 0150) the way ``recorder.py`` owns
``journal_events``.

WHAT THIS STORY SHIPS, AND WHAT IT DOES NOT. AD-28 describes a full public
``needs_you.resolve`` API: version/digest refusal, a concurrency-proof
owner-answer path, waiter delivery, an expiry sweep. That is Story 3.2's own
AC list in full. This module ships only the MINIMAL internal primitive
``record()`` itself needs to open an item on a ``NEEDS_YOU`` event and close
one on a registered ``resolves`` name -- :func:`open_item` and
:func:`_resolve_open_item`. Story 3.2 extends or promotes this into the real
public resolver; nothing here is the public API.

RACE SAFETY. :func:`open_item` and :func:`_resolve_open_item` are each ONE
SQL statement -- an ``INSERT ... ON CONFLICT(dedupe_key) WHERE resolved_cursor
IS NULL DO NOTHING RETURNING id`` and an ``UPDATE ... WHERE dedupe_key = ? AND
resolved_cursor IS NULL RETURNING id`` -- so "did this call actually win" is
answered by SQLite's own partial unique index and conditional UPDATE, never
by an application-level check-then-insert/update (spec Boundaries). The
partial unique index (migration 0150) is the ONLY thing enforcing "one open
item per target".

RETENTION HOLD. :func:`_held_cursors` is a zero-argument checker
(``RetentionHoldRegistry.HoldChecker``'s own shape), registered into
``get_retention_hold_registry()`` at the bottom of this module -- mirrors
``job_events.py``'s bottom-of-file ``get_record_reader_registry().register(...)``
side-effect convention. A zero-arg checker cannot take a live ``DbPool``
parameter, so this module holds its OWN module-global pool reference
(:func:`set_db_pool`), mirroring ``memory/curated.py``'s identically-shaped
seam for the same reason: the checker must be reachable with no caller-
supplied context, and ``None`` until wired is a safe no-op (an unwired
process holds nothing, rather than crashing the prune job that calls it).
Wired at boot by ``startup/orchestrator.py``'s ``_phase_gateway``, right next
to the identically-shaped ``memory/curated.py::set_db_pool`` call -- an
AST-guard test (``tests/startup/test_journal_name_resolver_wiring.py``)
pins that the real call site is never silently dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field

from stackowl.infra.observability import log
from stackowl.journal.enums import AttentionClass, Intensity, NeedsYouKind, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventTypeSpec, get_registry
from stackowl.journal.retention_holds import get_retention_hold_registry

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    import aiosqlite

    from stackowl.db.pool import DbPool

_EMITTING_PROCESS = "journal.recorder"
_TABLE = "needs_you"

#: AD-4's 64-char bound on a closed/bounded label, mirrored here for
#: `resolved_by` -- "system:{event.type}" stays well under it (the longest
#: real event type name today is far shorter).
_MAX_LABEL_LEN = 64

#: Ordered so the open-set query's `CASE` expression sorts HIGH before
#: NORMAL explicitly (AD-28: "ordered by intensity then opened_cursor") --
#: not left to the accident that "high" < "normal" lexically, which would
#: silently break if a future intensity value were ever added.
_INTENSITY_ORDER_SQL = (
    f"CASE intensity WHEN '{Intensity.HIGH.value}' THEN 0 ELSE 1 END"
)


class NeedsYouOpenedAttrs(JournalAttrsBase):
    """``needs_you.opened`` -- a durable item was just opened for a
    ``NEEDS_YOU`` event's target (AD-28)."""

    item_id: str
    kind: NeedsYouKind
    intensity: Intensity


class NeedsYouResolvedAttrs(JournalAttrsBase):
    """``needs_you.resolved`` -- a registered ``resolves`` name closed an
    already-open item (AD-28). ``resolved_by`` is always ``"system:{type}"``
    for this story's minimal internal resolver -- Story 3.2's public resolver
    is the first writer of an owner- or surface-attributed value."""

    item_id: str
    kind: NeedsYouKind
    #: AD-4: "bounded labels of at most 64 characters" -- every other attrs
    #: string field in this module and its siblings already has this bound;
    #: `_resolve_open_item`'s own DB-row write already truncates to the same
    #: limit (`resolved_by[:_MAX_LABEL_LEN]`), so this keeps the typed model
    #: honest about the same ceiling rather than silently allowing more.
    resolved_by: str = Field(max_length=_MAX_LABEL_LEN)


def _narrate_opened(attrs: JournalAttrsBase, name: str) -> str:
    a = cast(NeedsYouOpenedAttrs, attrs)
    return f"A {a.kind.value} item opened for {name}."


def _narrate_resolved(attrs: JournalAttrsBase, name: str) -> str:
    a = cast(NeedsYouResolvedAttrs, attrs)
    return f"The {a.kind.value} item for {name} was resolved."


def _register() -> None:
    registry = get_registry()
    # AD-28's own boundary, verbatim: opening/closing a needs-you item must
    # never itself open one -- both types are AMBIENT, never NEEDS_YOU, so
    # `record()`'s own recursive wiring never recurses a second level.
    registry.register(EventTypeSpec(
        type="needs_you.opened", schema_version=1, attrs_model=NeedsYouOpenedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.NEEDS_YOU,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_opened,
    ))
    registry.register(EventTypeSpec(
        type="needs_you.resolved", schema_version=1, attrs_model=NeedsYouResolvedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.NEEDS_YOU,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_resolved,
    ))


_register()


def _dedupe_key(kind: str, target_kind: str, target_id: str) -> str:
    """One open item per (kind, target) (AD-28). ``kind`` is a
    :class:`NeedsYouKind` value, not the emitting event's own type name --
    two different give-up TYPES for the same target and the same kind
    (unlikely today, structurally possible later) still dedupe onto one item.
    """
    return f"{kind}:{target_kind}:{target_id}"


async def open_item(
    conn: aiosqlite.Connection,
    *,
    kind: NeedsYouKind,
    intensity: Intensity,
    dedupe_key: str,
    record_ref_json: str | None,
    opened_cursor: int,
) -> str | None:
    """Open one durable item, or no-op if one is already open for
    ``dedupe_key`` (AD-28's partial unique index does the enforcing, not this
    function). Returns the new item's id on a real open, ``None`` on a
    duplicate no-op.

    ``record_ref_json`` is the ALREADY-redacted JSON text ``recorder.py``
    computed for the triggering event's own ``record_ref`` column -- reused
    directly rather than re-serialized, so a needs_you row can never carry an
    unredacted copy of a locator the journal row itself redacted.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] needs_you.open_item: entry",
        extra={"_fields": {"kind": kind.value, "dedupe_key": dedupe_key}},
    )
    from stackowl.journal.ids import new_event_id

    item_id = new_event_id()
    # 2. DECISION / 3. STEP -- one INSERT, conditional on the partial unique
    # index over UNRESOLVED rows. `RETURNING id` makes "did this call win"
    # answerable from the statement's own result set -- no follow-up SELECT,
    # so no window for a second writer to race between insert and read.
    cursor = await conn.execute(
        "INSERT INTO needs_you "
        "(id, kind, intensity, record_ref, dedupe_key, version, opened_cursor) "
        "VALUES (?, ?, ?, ?, ?, 1, ?) "
        "ON CONFLICT(dedupe_key) WHERE resolved_cursor IS NULL DO NOTHING "
        "RETURNING id",
        (item_id, kind.value, intensity.value, record_ref_json, dedupe_key, opened_cursor),
    )
    row = await cursor.fetchone()
    won = row is not None
    # 4. EXIT
    log.journal.info(
        "[journal] needs_you.open_item: exit",
        extra={"_fields": {
            "kind": kind.value, "dedupe_key": dedupe_key, "opened": won,
        }},
    )
    return item_id if won else None


async def _resolve_open_item(
    conn: aiosqlite.Connection,
    *,
    dedupe_key: str,
    resolved_cursor: int,
    resolved_by: str,
) -> str | None:
    """The minimal internal resolver: closes the one open item for
    ``dedupe_key``, if any (AD-28's conditional UPDATE, ``record()``'s own
    ``resolves`` wiring). Returns the resolved item's id, or ``None`` when
    nothing was open for this key.

    NOT the public ``needs_you.resolve`` -- no version/digest check, no
    waiter delivery, no ``answer`` write. Story 3.2's own AC list (spec
    Boundaries).
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] needs_you._resolve_open_item: entry",
        extra={"_fields": {"dedupe_key": dedupe_key}},
    )
    # 2. DECISION / 3. STEP -- one conditional UPDATE; `RETURNING id` answers
    # "did this call win" from the statement's own result, same reasoning as
    # `open_item` above.
    cursor = await conn.execute(
        "UPDATE needs_you SET resolved_cursor = ?, resolved_by = ? "
        "WHERE dedupe_key = ? AND resolved_cursor IS NULL "
        "RETURNING id",
        (resolved_cursor, resolved_by[:_MAX_LABEL_LEN], dedupe_key),
    )
    row = await cursor.fetchone()
    item_id = row["id"] if row is not None else None
    # 4. EXIT
    log.journal.info(
        "[journal] needs_you._resolve_open_item: exit",
        extra={"_fields": {"dedupe_key": dedupe_key, "resolved": item_id is not None}},
    )
    return item_id


class NeedsYouItemView(BaseModel):
    """Typed view of one ``needs_you`` row -- :func:`open_items`'s return
    shape. No registered ``RecordReader`` exists for ``RecordKind.NEEDS_YOU``
    yet (spec Boundaries: "no AC needs one yet") -- this is a direct table
    view for the open-set query, not that reader.

    ``kind``/``intensity`` are the real :class:`NeedsYouKind`/:class:`Intensity`
    enums, not plain ``str`` -- pydantic coerces the matching DB string
    automatically, so a caller of :func:`open_items` gets the same type
    safety the write-side ``NeedsYouOpenedAttrs``/``NeedsYouResolvedAttrs``
    models already have.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    kind: NeedsYouKind
    intensity: Intensity
    record_ref: str | None = None
    dedupe_key: str
    waiter_kind: str | None = None
    waiter_id: str | None = None
    expires_at: str | None = None
    version: int
    opened_cursor: int
    resolved_cursor: int | None = None
    answer: str | None = None
    resolved_by: str | None = None


async def open_items(db_pool: DbPool) -> list[NeedsYouItemView]:
    """Every unresolved item, ordered by intensity (high first) then
    ``opened_cursor`` ascending (AD-28)."""
    # 1. ENTRY
    log.journal.debug("[journal] needs_you.open_items: entry")
    rows = await db_pool.fetch_all(
        "SELECT * FROM needs_you WHERE resolved_cursor IS NULL "
        f"ORDER BY {_INTENSITY_ORDER_SQL}, opened_cursor ASC",
        (),
    )
    items = [NeedsYouItemView.model_validate(dict(row)) for row in rows]
    # 4. EXIT
    log.journal.debug(
        "[journal] needs_you.open_items: exit",
        extra={"_fields": {"count": len(items)}},
    )
    return items


#: Story 3.1 -- module-global pool, wired once at startup (mirrors
#: `memory/curated.py::set_db_pool`'s identically-shaped seam; see module
#: docstring for why the retention-hold checker needs one). `None` until
#: wired: a test or standalone construction that never called `set_db_pool`
#: holds nothing, rather than crashing the prune job that calls it.
_DB_POOL: DbPool | None = None


def set_db_pool(db_pool: DbPool) -> None:
    """Wire the DbPool :func:`_held_cursors` reads through. Called once at
    startup (``startup/orchestrator.py``), mirroring
    ``memory/curated.py::set_db_pool``'s own seam."""
    global _DB_POOL  # noqa: PLW0603 -- one process-wide pool, deliberately
    _DB_POOL = db_pool


async def _held_cursors() -> frozenset[int]:
    """The retention-hold checker (AD-6): every ``opened_cursor`` an
    unresolved item still references, so ``journal_prune`` never deletes the
    give-up event that opened it. Returns an empty set when no pool is wired
    -- the same safe-no-op shape every other module-global-pool reader in
    this codebase takes.
    """
    if _DB_POOL is None:
        return frozenset()
    rows = await _DB_POOL.fetch_all(
        "SELECT opened_cursor FROM needs_you WHERE resolved_cursor IS NULL", (),
    )
    return frozenset(int(row["opened_cursor"]) for row in rows)


get_retention_hold_registry().register_hold_source("needs_you", _held_cursors)
