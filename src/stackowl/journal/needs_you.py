"""Durable Needs-you items -- open/resolve/query primitives, and the
``needs_you.opened``/``needs_you.resolved`` event types themselves
(Story 3.1, Story 3.2, AD-28).

PLACEMENT: inside ``journal/`` itself -- AD-7 names "Needs-you items and
resolver" explicitly in ``journal/``'s own package inventory. This module
owns the ``needs_you`` table (migration 0150) the way ``recorder.py`` owns
``journal_events``.

WHAT STORY 3.1 SHIPPED, AND WHAT STORY 3.2 ADDS. AD-28 describes a full
public ``needs_you.resolve`` API: version/digest refusal, a concurrency-proof
owner-answer path, waiter delivery, an expiry sweep. Story 3.1 shipped only
the MINIMAL internal primitive ``record()`` itself needs to open an item on a
``NEEDS_YOU`` event and close one on a registered ``resolves`` name --
:func:`open_item` and :func:`_resolve_open_item`. Story 3.2 adds the real
public API on top of that same table: :func:`resolve` (the one entry point
every owner-facing surface -- Telegram, the Bridge strip, voice, a
notification deep link -- calls to actually answer an item),
:func:`compute_item_digest`, :func:`_expire_item` (shared by ``resolve()``'s
own expiry branch and the sweep) and :func:`sweep_expired_items` (the seeded
job's own body).

STORY 3.3 gives ``resolve()`` its first real callers (``tools/consent.py``,
``interaction/clarify_gateway.py``) and adds three more primitives:
:func:`bind_waiter` (binds ``waiter_kind``/``waiter_id``/``expires_at`` onto
an item ``record()``'s own generic wiring just opened, in the SAME
transaction -- a second statement rather than widening ``open_item()`` or
``journal.record()``'s own public signature); :func:`expire_stranded_turn_waiters`
(the boot-time sweep that resolves every still-open ``waiter_kind="turn"``
item as ``expired`` -- the in-memory waiter that opened it belonged to a now
-gone process); and :func:`settle_or_abandon` (the shared, never-raising
primitive both real call sites' non-happy-path exits use to settle a bound
item when the local wait itself gives up -- timeout, cancel, or a
prompter/gateway raising -- without a real answer, distinct from a boot
restart or a TTL expiry only by its ``resolved_by`` label).

RACE SAFETY. :func:`open_item` and :func:`_resolve_open_item` are each ONE
SQL statement -- an ``INSERT ... ON CONFLICT(dedupe_key) WHERE resolved_cursor
IS NULL DO NOTHING RETURNING id`` and an ``UPDATE ... WHERE dedupe_key = ? AND
resolved_cursor IS NULL RETURNING id`` -- so "did this call actually win" is
answered by SQLite's own partial unique index and conditional UPDATE, never
by an application-level check-then-insert/update (spec Boundaries). The
partial unique index (migration 0150) is the ONLY thing enforcing "one open
item per target". :func:`resolve` and :func:`_expire_item` carry the SAME
shape forward: one conditional ``UPDATE ... WHERE id = ? AND resolved_cursor
IS NULL [AND version = ?] RETURNING ...`` decides who won -- never a prior
``SELECT`` followed by a separate ``UPDATE`` with no WHERE-clause guard. A
prior ``SELECT`` may still inform an early, cheap REFUSAL (a stale version or
digest) -- but never the actual win/lose decision, which is always the
conditional UPDATE's own affected-row count.

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

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from stackowl.exceptions import NeedsYouItemNotFoundError
from stackowl.infra.observability import log, redact_secret_shapes
from stackowl.journal.enums import (
    ActorKind,
    AttentionClass,
    Intensity,
    NeedsYouKind,
    Outcome,
    RecordKind,
)
from stackowl.journal.models import JournalAttrsBase, JournalEvent, RecordRef
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

#: Story 3.3 -- the ONLY `waiter_kind` that exists until Epic 4's durable
#: COMMAND-task waiters: an in-memory `asyncio.Future`/`asyncio.Event`
#: belonging to a live turn, gone the moment its process restarts (spec
#: Intent). Shared by :func:`bind_waiter`'s real callers,
#: :func:`expire_stranded_turn_waiters`'s own query, and this module's
#: settle-on-abandon primitive's docstring -- never a bare string literal
#: repeated at each call site (review pass 1, low finding).
WAITER_KIND_TURN = "turn"


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


async def bind_waiter(
    conn: aiosqlite.Connection,
    *,
    kind: NeedsYouKind,
    target_kind: str,
    target_id: str,
    waiter_kind: str,
    waiter_id: str,
    expires_at: str,
) -> str | None:
    """Bind a waiter onto the item ``record()``'s own generic wiring just
    opened for ``(kind, target_kind, target_id)`` -- a SECOND statement in
    the SAME transaction as that opening event (Design Notes: "a second
    statement, not a wider ``open_item()``" -- ``journal.record()``'s public
    signature and ``open_item()``'s own parameters stay untouched; spec
    Boundaries).

    One conditional ``UPDATE ... WHERE dedupe_key = ? AND resolved_cursor IS
    NULL RETURNING id`` -- the SAME race-safety shape
    :func:`_resolve_open_item` already uses. ``dedupe_key`` is recomputed
    here from ``(kind, target_kind, target_id)`` via :func:`_dedupe_key`, the
    identical formula ``record()``'s own NEEDS_YOU wiring used to open the
    row a moment earlier in this same transaction -- so the caller must pass
    the SAME ``kind``/``target_kind``/``target_id`` it used to trigger that
    opening event.

    Returns the bound item's id, or ``None`` if nothing is open for this
    ``dedupe_key`` (a caller bug -- the triggering event's own
    ``journal.record()`` call must have opened it moments earlier in this
    same transaction).
    """
    # Review finding: `journal.record()`'s own NEEDS_YOU wiring (recorder.py)
    # computes the OPENING dedupe_key from the REDACTED target_id, not the
    # raw one -- redact here too, or a target_id that ever matched a
    # secret-shaped pattern would make the two keys diverge and this bind
    # would silently fail to match the row `open_item()` just inserted.
    redacted_target_id, _ = redact_secret_shapes(target_id)
    dedupe_key = _dedupe_key(kind.value, target_kind, redacted_target_id)
    # 1. ENTRY
    log.journal.debug(
        "[journal] needs_you.bind_waiter: entry",
        extra={"_fields": {"dedupe_key": dedupe_key, "waiter_kind": waiter_kind}},
    )
    # 2. DECISION / 3. STEP -- one conditional UPDATE; `RETURNING id` answers
    # "did this call actually bind something" from the statement's own
    # result, same reasoning as `open_item`/`_resolve_open_item` above.
    cursor = await conn.execute(
        "UPDATE needs_you SET waiter_kind = ?, waiter_id = ?, expires_at = ? "
        "WHERE dedupe_key = ? AND resolved_cursor IS NULL "
        "RETURNING id",
        (waiter_kind, waiter_id, expires_at, dedupe_key),
    )
    row = await cursor.fetchone()
    item_id = row["id"] if row is not None else None
    # 4. EXIT
    log.journal.info(
        "[journal] needs_you.bind_waiter: exit",
        extra={"_fields": {"dedupe_key": dedupe_key, "bound": item_id is not None}},
    )
    return item_id


#: Story 3.2 -- the sentinel every claim-UPDATE stamps into ``resolved_cursor``
#: to win the race, fixed up to the REAL journal cursor only by the winner,
#: still inside the same open transaction (Design Notes: "claim, then fix
#: up"). ``journal_events.cursor`` is ``INTEGER PRIMARY KEY AUTOINCREMENT``,
#: so 0 is never a real cursor value -- safe to use as a "claimed but not yet
#: fixed up" marker that can never collide with a genuine one.
_CLAIM_SENTINEL = 0

#: Story 3.2 -- the fixed ``resolved_by`` label the expiry sweep stamps on a
#: ``needs_you`` row and its ``NeedsYouResolvedAttrs.resolved_by`` (AD-4's
#: 64-char bound; well under it). Mirrors ``_resolve_open_item``'s own
#: ``"system:{type}"`` convention -- expiry has no answering surface to
#: attribute to, so it attributes to itself.
_EXPIRY_RESOLVED_BY = "system:needs_you_expiry_sweep"


class NeedsYouResolution(BaseModel):
    """The outcome of one :func:`resolve` (or :func:`_expire_item`) call --
    the ONLY thing that could ever be delivered to an item's waiter (spec
    Boundaries: no IPC/frame-sending happens here, that is a future caller's
    job). ``outcome="resolved"``/``"expired"`` both mean the item is now
    settled (whether by THIS call or an earlier winner); ``"refused"`` means
    the item is still open, re-shown at its current ``version``.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    outcome: Literal["resolved", "expired", "refused"]
    answer: str | None
    version: int
    kind: NeedsYouKind
    waiter_kind: str | None
    waiter_id: str | None


def compute_item_digest(
    kind: NeedsYouKind, record_ref_json: str | None, version: int,
) -> str:
    """A short, deterministic digest of one item's ``(kind, record_ref,
    version)`` -- mirrors ``journal/digest.py``'s own
    ``compute_registry_digest`` shape exactly (first 16 hex chars of a sha256
    over a canonical string). COMPUTED on demand, never stored -- AD-28's own
    closed ``needs_you`` column list has no ``digest`` column (spec
    Boundaries) -- so a version bump automatically changes the digest too,
    with no second field to keep in sync.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] needs_you.compute_item_digest: entry",
        extra={"_fields": {"kind": kind.value, "version": version}},
    )
    # 2. STEP -- one canonical string, one hash; no branching.
    canonical = f"{kind.value}:{record_ref_json or ''}:{version}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    # 4. EXIT
    log.journal.debug(
        "[journal] needs_you.compute_item_digest: exit",
        extra={"_fields": {"kind": kind.value, "version": version, "digest": digest}},
    )
    return digest


async def _journal_cursor_for_event_id(
    conn: aiosqlite.Connection, event_id: str,
) -> int:
    """Look up the numeric ``journal_events.cursor`` for an ``event_id`` this
    same transaction just inserted via ``journal.record()`` (AD-24/spec
    Boundaries: never widen ``record()``'s own public return type to expose
    the cursor -- look it up on the SAME ``conn`` via the indexed UNIQUE
    ``event_id`` column instead). The row is guaranteed to exist -- it was
    inserted a moment earlier in this same open transaction; a loud raise
    (never a bare ``assert``, stripped under ``python -O``) documents that
    invariant rather than silently trusting it.
    """
    cursor_rows = await conn.execute(
        "SELECT cursor FROM journal_events WHERE event_id = ?", (event_id,),
    )
    cursor_row = await cursor_rows.fetchone()
    if cursor_row is None:
        raise RuntimeError(
            f"needs_you._journal_cursor_for_event_id: event_id={event_id!r} "
            "has no journal_events row -- record() must have inserted it a "
            "moment earlier in this same transaction"
        )
    return int(cursor_row["cursor"])


async def _reread_settled_or_refused(
    conn: aiosqlite.Connection, *, item_id: str,
) -> NeedsYouResolution:
    """Shared by :func:`resolve` and :func:`_expire_item`'s own LOSING branch:
    after a claim-UPDATE affects 0 rows, some other writer won first (in the
    same transaction race, or a genuine version bump between the caller's own
    prior SELECT and its UPDATE attempt) -- re-read the row fresh and return
    whichever outcome actually won, rather than re-deciding anything at the
    application level.

    The row is guaranteed to still exist -- nothing in this codebase ever
    deletes a ``needs_you`` row, and this is only ever called on an id that
    was just SELECTed a moment earlier in the same transaction.
    """
    rows = await conn.execute("SELECT * FROM needs_you WHERE id = ?", (item_id,))
    row = await rows.fetchone()
    if row is None:
        # Unreachable in practice (see docstring) -- explicit, loud raise
        # rather than `assert` (stripped under `python -O`; no existing
        # precedent for bare `assert`-as-narrowing anywhere in
        # `journal/*.py`, mirroring `recorder.py`'s own convention).
        raise RuntimeError(
            f"needs_you._reread_settled_or_refused: item_id={item_id!r} "
            "vanished mid-transaction -- nothing in this codebase ever "
            "deletes a needs_you row"
        )
    kind = NeedsYouKind(row["kind"])
    current_version = int(row["version"])
    waiter_kind = row["waiter_kind"]
    waiter_id = row["waiter_id"]
    if row["resolved_cursor"] is not None:
        # Both real outcomes set resolved_cursor; only a genuine answer ever
        # sets `answer` non-NULL (Design Notes) -- that alone disambiguates
        # "resolved" from "expired" here, with no extra column.
        outcome: Literal["resolved", "expired"] = (
            "resolved" if row["answer"] is not None else "expired"
        )
        return NeedsYouResolution(
            item_id=item_id, outcome=outcome, answer=row["answer"],
            version=current_version, kind=kind,
            waiter_kind=waiter_kind, waiter_id=waiter_id,
        )
    # Still unresolved: the claim-UPDATE's own `AND version = ?` guard must be
    # what failed (a concurrent version bump between the caller's SELECT and
    # its UPDATE) -- refused, re-shown with the fresh current version.
    return NeedsYouResolution(
        item_id=item_id, outcome="refused", answer=None,
        version=current_version, kind=kind,
        waiter_kind=waiter_kind, waiter_id=waiter_id,
    )


def _require_tz_aware_or_none(now: datetime | None, *, caller: str) -> None:
    """Every real ``expires_at`` value is an offset-bearing ISO8601 string
    (``_now_iso()``'s own shape, mirrored by every ``now.isoformat()`` call
    in this module) -- ``expires_at <= now_iso`` is a plain STRING compare,
    and a naive ``datetime``'s ``isoformat()`` carries no offset, so it would
    sort as a PREFIX of (less than) every real value and silently
    under-trigger expiry instead of comparing correctly. A caller bug, not a
    protocol outcome -- raised loudly before any I/O, never swallowed by a
    surrounding try/except.
    """
    if now is not None and now.tzinfo is None:
        raise ValueError(
            f"needs_you.{caller}: now must be timezone-aware (or None) -- a "
            "naive datetime would silently corrupt the expires_at comparison"
        )


async def resolve(
    conn: aiosqlite.Connection,
    *,
    item_id: str,
    answer: str,
    resolved_by: str,
    expected_version: int | None = None,
    expected_digest: str | None = None,
    now: datetime | None = None,
) -> NeedsYouResolution:
    """The public resolver (Story 3.2, AD-28) -- the ONE way any owner-facing
    surface (Telegram, the Bridge strip, voice, a notification deep link)
    answers a Needs-you item. Every surface calls only this function; an
    answer is never enqueued as a COMMAND task (spec Boundaries).

    Never raises for a legitimate protocol outcome -- already resolved,
    already expired, or refused on a stale version/digest all come back as
    :class:`NeedsYouResolution` values. Raises only
    :class:`~stackowl.exceptions.NeedsYouItemNotFoundError` for an
    ``item_id`` no row has at all (a caller bug).

    Runs on the CALLER's own open transaction (``conn``), same as
    :func:`open_item`/:func:`_resolve_open_item` -- never opens or commits
    one itself. A winning resolution's ``needs_you.resolved`` write and the
    row's own ``UPDATE`` land in that SAME transaction (AD-24).
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] needs_you.resolve: entry",
        extra={"_fields": {"item_id": item_id}},
    )
    _require_tz_aware_or_none(now, caller="resolve")
    now_dt = now if now is not None else datetime.now(UTC)
    now_iso = now_dt.isoformat()
    resolved_by = resolved_by[:_MAX_LABEL_LEN]

    rows = await conn.execute("SELECT * FROM needs_you WHERE id = ?", (item_id,))
    row = await rows.fetchone()
    if row is None:
        log.journal.warning(
            "[journal] needs_you.resolve: item_id not found -- caller bug",
            extra={"_fields": {"item_id": item_id}},
        )
        raise NeedsYouItemNotFoundError(item_id)

    kind = NeedsYouKind(row["kind"])
    current_version = int(row["version"])
    record_ref_json = row["record_ref"]
    waiter_kind = row["waiter_kind"]
    waiter_id = row["waiter_id"]
    expires_at = row["expires_at"]

    # 2. DECISION -- already settled: return the winning outcome unchanged,
    # no write. (I/O matrix: "already resolved" / "already expired".)
    if row["resolved_cursor"] is not None:
        outcome: Literal["resolved", "expired"] = (
            "resolved" if row["answer"] is not None else "expired"
        )
        log.journal.info(
            "[journal] needs_you.resolve: exit -- already settled",
            extra={"_fields": {"item_id": item_id, "outcome": outcome}},
        )
        return NeedsYouResolution(
            item_id=item_id, outcome=outcome, answer=row["answer"],
            version=current_version, kind=kind,
            waiter_kind=waiter_kind, waiter_id=waiter_id,
        )

    # Expiry wins over anything the caller submitted -- resolves through the
    # SAME conditional-UPDATE path, ignoring the submitted answer/version/
    # digest entirely (I/O matrix: "answer arrives after expires_at").
    if expires_at is not None and expires_at <= now_iso:
        log.journal.info(
            "[journal] needs_you.resolve: item is past expires_at -- "
            "routing through _expire_item instead of the submitted answer",
            extra={"_fields": {"item_id": item_id}},
        )
        return await _expire_item(conn, item_id=item_id, now=now_dt)

    # A cheap, early REFUSAL -- informed by the SELECT above, but never the
    # win/lose decision itself (spec Boundaries: that is the claim-UPDATE's
    # own affected-row count, below).
    if expected_version is not None and expected_version != current_version:
        log.journal.info(
            "[journal] needs_you.resolve: exit -- version mismatch, refused",
            extra={"_fields": {
                "item_id": item_id, "expected_version": expected_version,
                "current_version": current_version,
            }},
        )
        return NeedsYouResolution(
            item_id=item_id, outcome="refused", answer=None,
            version=current_version, kind=kind,
            waiter_kind=waiter_kind, waiter_id=waiter_id,
        )
    if expected_digest is not None:
        current_digest = compute_item_digest(kind, record_ref_json, current_version)
        if expected_digest != current_digest:
            log.journal.info(
                "[journal] needs_you.resolve: exit -- digest mismatch, refused",
                extra={"_fields": {"item_id": item_id}},
            )
            return NeedsYouResolution(
                item_id=item_id, outcome="refused", answer=None,
                version=current_version, kind=kind,
                waiter_kind=waiter_kind, waiter_id=waiter_id,
            )

    # 3. STEP -- claim the win with ONE conditional UPDATE (Design Notes:
    # "claim, then fix up"). The WHERE clause repeats the version check at
    # the SQL layer -- a genuine race between the SELECT above and this
    # UPDATE (another resolve()/the expiry sweep winning first) loses HERE,
    # never at the application level.
    redacted_answer, was_redacted = redact_secret_shapes(answer)
    if was_redacted:
        log.journal.warning(
            "[journal] needs_you.resolve: secret-shaped string redacted from "
            "answer before storage",
            extra={"_fields": {"item_id": item_id}},
        )
    claim_cursor = await conn.execute(
        "UPDATE needs_you SET resolved_cursor = ?, answer = ?, resolved_by = ? "
        "WHERE id = ? AND resolved_cursor IS NULL AND version = ? "
        "RETURNING id",
        (_CLAIM_SENTINEL, redacted_answer, resolved_by, item_id, current_version),
    )
    claimed = await claim_cursor.fetchone()
    if claimed is None:
        log.journal.info(
            "[journal] needs_you.resolve: lost the claim race -- re-reading "
            "the winner's outcome",
            extra={"_fields": {"item_id": item_id}},
        )
        return await _reread_settled_or_refused(conn, item_id=item_id)

    # Only the winner reaches here. journal.record() is imported lazily --
    # recorder.py imports this module at its own top level, so a top-level
    # import here would be circular; by the time resolve() is ever CALLED
    # the whole journal package has already finished loading.
    from stackowl.journal.recorder import record as _record

    event_id = await _record(
        conn,
        JournalEvent(
            type="needs_you.resolved", schema_version=1,
            occurred_at=now_iso,
            actor_kind=ActorKind.OWNER, actor_id=resolved_by,
            target_kind=ActorKind.OWNER, target_id=item_id,
            outcome=Outcome.OK,
            record_ref=RecordRef(
                kind="sqlite", locator={"table": "needs_you", "id": item_id},
            ),
            attrs=NeedsYouResolvedAttrs(item_id=item_id, kind=kind, resolved_by=resolved_by),
        ),
    )
    real_cursor = await _journal_cursor_for_event_id(conn, event_id)
    await conn.execute(
        "UPDATE needs_you SET resolved_cursor = ? WHERE id = ?",
        (real_cursor, item_id),
    )

    # 4. EXIT
    log.journal.info(
        "[journal] needs_you.resolve: exit -- won",
        extra={"_fields": {"item_id": item_id, "outcome": "resolved"}},
    )
    return NeedsYouResolution(
        item_id=item_id, outcome="resolved", answer=redacted_answer,
        version=current_version, kind=kind,
        waiter_kind=waiter_kind, waiter_id=waiter_id,
    )


async def _expire_item(
    conn: aiosqlite.Connection,
    *,
    item_id: str,
    now: datetime | None = None,
    resolved_by: str = _EXPIRY_RESOLVED_BY,
) -> NeedsYouResolution:
    """Resolve one item as expired, through the SAME claim-then-fix-up shape
    :func:`resolve`'s own winning branch uses (Design Notes) -- shared by
    that branch, :func:`sweep_expired_items`, :func:`expire_stranded_turn_waiters`
    and :func:`settle_or_abandon`. No version/digest check: expiry overrides
    whatever version the item was last shown at, and never sets ``answer``
    (Design Notes: "expiry never sets ``answer``... this lets the 'already
    resolved' branch tell the two apart from one SELECT, with no new
    column").

    ``resolved_by`` defaults to the periodic sweep's own label
    (:data:`_EXPIRY_RESOLVED_BY`) -- Story 3.3's other two callers pass their
    own distinct label so a reader of a settled row can tell a
    time-based expiry apart from a boot-detected stranded waiter or a local
    abandonment (timeout/cancel/prompter error), even though all three settle
    through this identical shape.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] needs_you._expire_item: entry",
        extra={"_fields": {"item_id": item_id, "resolved_by": resolved_by}},
    )
    _require_tz_aware_or_none(now, caller="_expire_item")
    now_iso = (now if now is not None else datetime.now(UTC)).isoformat()
    resolved_by = resolved_by[:_MAX_LABEL_LEN]

    # 2/3. DECISION+STEP -- one conditional UPDATE, `RETURNING *` since
    # (unlike resolve()'s own winning branch) there is no prior SELECT to
    # reuse kind/version/waiter_kind/waiter_id from.
    claim_cursor = await conn.execute(
        "UPDATE needs_you SET resolved_cursor = ?, resolved_by = ? "
        "WHERE id = ? AND resolved_cursor IS NULL "
        "RETURNING *",
        (_CLAIM_SENTINEL, resolved_by, item_id),
    )
    claimed = await claim_cursor.fetchone()
    if claimed is None:
        result = await _reread_settled_or_refused(conn, item_id=item_id)
        log.journal.info(
            "[journal] needs_you._expire_item: exit -- already settled by "
            "another writer",
            extra={"_fields": {"item_id": item_id, "outcome": result.outcome}},
        )
        return result

    kind = NeedsYouKind(claimed["kind"])
    version = int(claimed["version"])
    waiter_kind = claimed["waiter_kind"]
    waiter_id = claimed["waiter_id"]

    from stackowl.journal.recorder import record as _record

    event_id = await _record(
        conn,
        JournalEvent(
            type="needs_you.resolved", schema_version=1,
            occurred_at=now_iso,
            actor_kind=ActorKind.AUTONOMOUS, actor_id=resolved_by,
            target_kind=ActorKind.OWNER, target_id=item_id,
            outcome=Outcome.EXPIRED,
            record_ref=RecordRef(
                kind="sqlite", locator={"table": "needs_you", "id": item_id},
            ),
            attrs=NeedsYouResolvedAttrs(
                item_id=item_id, kind=kind, resolved_by=resolved_by,
            ),
        ),
    )
    real_cursor = await _journal_cursor_for_event_id(conn, event_id)
    await conn.execute(
        "UPDATE needs_you SET resolved_cursor = ? WHERE id = ?",
        (real_cursor, item_id),
    )

    # 4. EXIT
    log.journal.info(
        "[journal] needs_you._expire_item: exit -- expired",
        extra={"_fields": {"item_id": item_id}},
    )
    return NeedsYouResolution(
        item_id=item_id, outcome="expired", answer=None,
        version=version, kind=kind,
        waiter_kind=waiter_kind, waiter_id=waiter_id,
    )


async def sweep_expired_items(db_pool: DbPool, *, now: datetime | None = None) -> list[str]:
    """Expire every unresolved item whose ``expires_at`` has passed (AD-28).

    Each item resolves in its OWN transaction -- a failure partway through a
    large sweep must not roll back items already expired earlier in the same
    pass (I/O matrix: "one ``needs_you.resolved`` per item, in its own
    transaction"). Never raises OUT OF A DB/infra failure: the seeded
    scheduler job (:mod:`stackowl.scheduler.handlers.needs_you_expiry_sweep`)
    is this function's only production caller and must never fail a tick
    over one bad row or a failed candidate query, and :func:`open_items`' own
    opportunistic sweep call needs the same guarantee. (A caller bug -- a
    naive ``now`` -- still raises loudly; see :func:`_require_tz_aware_or_none`.)
    Returns the ids that actually expired this pass.
    """
    # 1. ENTRY
    log.journal.debug("[journal] needs_you.sweep_expired_items: entry")
    _require_tz_aware_or_none(now, caller="sweep_expired_items")
    now_iso = (now if now is not None else datetime.now(UTC)).isoformat()
    try:
        candidates = await db_pool.fetch_all(
            "SELECT id FROM needs_you WHERE resolved_cursor IS NULL "
            "AND expires_at IS NOT NULL AND expires_at <= ?",
            (now_iso,),
        )
    except Exception as exc:  # noqa: BLE001 -- see docstring: never raise out of a DB failure
        log.journal.warning(
            "[journal] needs_you.sweep_expired_items: candidate query failed "
            "-- treating this pass as zero candidates",
            exc_info=exc,
        )
        return []
    # 2. DECISION -- one item at a time, each its own transaction (see
    # docstring); a per-item failure is logged and skipped, never fatal to
    # the rest of the pass.
    expired_ids: list[str] = []
    for candidate in candidates:
        item_id = candidate["id"]
        try:
            # 3. STEP
            async with db_pool.transaction() as conn:
                result = await _expire_item(conn, item_id=item_id, now=now)
            if result.outcome == "expired":
                expired_ids.append(item_id)
        except Exception as exc:  # noqa: BLE001 -- one bad row must not sink the pass
            log.journal.warning(
                "[journal] needs_you.sweep_expired_items: one item failed to "
                "expire -- continuing with the rest of the pass",
                exc_info=exc, extra={"_fields": {"item_id": item_id}},
            )
    # 4. EXIT
    log.journal.info(
        "[journal] needs_you.sweep_expired_items: exit",
        extra={"_fields": {
            "candidate_count": len(candidates), "expired_count": len(expired_ids),
        }},
    )
    return expired_ids


#: Story 3.3 (AD-28) -- the fixed ``resolved_by`` label the BOOT sweep stamps
#: on every stranded ``waiter_kind="turn"`` item -- distinct from
#: :data:`_EXPIRY_RESOLVED_BY` (the PERIODIC time-based sweep's own label) so
#: a reader of a settled row can tell "this process restarted and the waiter
#: was provably gone" apart from an ordinary TTL expiry.
_BOOT_STRANDED_RESOLVED_BY = "system:expire_stranded_turn_waiters"


async def expire_stranded_turn_waiters(db_pool: DbPool) -> list[str]:
    """At boot, resolve every still-open ``waiter_kind="turn"`` item as
    ``expired`` UNCONDITIONALLY -- ignoring ``expires_at`` entirely (spec
    Intent): the in-memory ``asyncio.Future``/``asyncio.Event`` that opened
    it belonged to a process that is now provably gone, whether or not its
    own TTL has technically elapsed yet. Called once, at boot, BEFORE the
    gateway starts accepting turns (``startup/orchestrator.py``'s
    ``_phase_gateway``, right after :func:`set_db_pool`) -- Epic 4's durable
    COMMAND-task ``waiter_kind`` is the only kind that will ever
    re-materialise instead of stranding here.

    Same shape as :func:`sweep_expired_items` (Code Map: "never-raise shape
    mirroring ``sweep_expired_items()``"): one item per own transaction, a
    per-item or candidate-query failure is logged and never fatal -- a single
    bad row must never be able to sink boot. Returns the ids that actually
    expired this pass.
    """
    # 1. ENTRY
    log.journal.debug("[journal] needs_you.expire_stranded_turn_waiters: entry")
    try:
        candidates = await db_pool.fetch_all(
            "SELECT id FROM needs_you WHERE resolved_cursor IS NULL "
            "AND waiter_kind = ?",
            (WAITER_KIND_TURN,),
        )
    except Exception as exc:  # noqa: BLE001 -- never raise out of boot
        log.journal.warning(
            "[journal] needs_you.expire_stranded_turn_waiters: candidate "
            "query failed -- treating this pass as zero candidates",
            exc_info=exc,
        )
        return []
    # 2. DECISION -- one item at a time, each its own transaction, exactly
    # like sweep_expired_items() -- a per-item failure is logged and skipped,
    # never fatal to the rest of boot.
    expired_ids: list[str] = []
    for candidate in candidates:
        item_id = candidate["id"]
        try:
            # 3. STEP
            async with db_pool.transaction() as conn:
                result = await _expire_item(
                    conn, item_id=item_id, resolved_by=_BOOT_STRANDED_RESOLVED_BY,
                )
            if result.outcome == "expired":
                expired_ids.append(item_id)
        except Exception as exc:  # noqa: BLE001 -- one bad row must not sink boot
            log.journal.warning(
                "[journal] needs_you.expire_stranded_turn_waiters: one item "
                "failed to expire -- continuing with the rest of boot",
                exc_info=exc, extra={"_fields": {"item_id": item_id}},
            )
    # 4. EXIT
    log.journal.info(
        "[journal] needs_you.expire_stranded_turn_waiters: exit",
        extra={"_fields": {
            "candidate_count": len(candidates), "expired_count": len(expired_ids),
        }},
    )
    return expired_ids


async def settle_or_abandon(
    db_pool: DbPool | None,
    *,
    item_id: str | None,
    resolved_by: str,
    now: datetime | None = None,
) -> NeedsYouResolution | None:
    """Settle a bound item that stopped being waited on LOCALLY without a
    real answer -- a prompter/gateway raising, a timeout, a cancellation, or
    any other abandoned-wake (spec Design Notes: "abandonment ... must settle
    the item"). Reuses :func:`_expire_item`'s own claim-then-fix-up shape
    directly -- the settled-with-no-answer signature is identical to a
    time-based or boot-detected expiry, only ``resolved_by`` differs, so a
    reader of the row can tell the three apart.

    NEVER RAISES -- unlike :func:`resolve`/:func:`_expire_item` (which raise
    for a genuine caller bug), this is called from exception-handling and
    cleanup paths at every real call site
    (``tools.consent.ConsentPolicy.request``,
    ``interaction.clarify_gateway.ClarifyGateway.wait_for_answer``),
    including immediately before a re-raised ``asyncio.CancelledError`` --a
    settle failure here must never itself raise, or it would swallow that
    re-raise. A missing ``db_pool`` or ``item_id`` is a no-op (mirrors every
    other ``db_pool``-unwired convention in this codebase) -- returns
    ``None`` on either that or a genuine failure; returns the settled
    :class:`NeedsYouResolution` on success.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] needs_you.settle_or_abandon: entry",
        extra={"_fields": {"item_id": item_id, "resolved_by": resolved_by}},
    )
    # 2. DECISION -- unwired/unbound is a safe no-op, not a caller bug here:
    # both real call sites only HAVE an item_id when their own earlier
    # open+bind succeeded, and only HAVE a db_pool when one was wired at all.
    if db_pool is None or item_id is None:
        log.journal.debug(
            "[journal] needs_you.settle_or_abandon: exit -- no db_pool or "
            "item_id",
            extra={"_fields": {"item_id": item_id}},
        )
        return None
    try:
        # 3. STEP
        async with db_pool.transaction() as conn:
            result = await _expire_item(
                conn, item_id=item_id, now=now, resolved_by=resolved_by,
            )
    except Exception as exc:  # noqa: BLE001 -- see docstring: never raise, never swallow a re-raise
        log.journal.warning(
            "[journal] needs_you.settle_or_abandon: failed -- the item may "
            "remain open until its own expiry",
            exc_info=exc,
            extra={"_fields": {"item_id": item_id, "resolved_by": resolved_by}},
        )
        return None
    # 4. EXIT
    log.journal.info(
        "[journal] needs_you.settle_or_abandon: exit",
        extra={"_fields": {"item_id": item_id, "outcome": result.outcome}},
    )
    return result


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
    ``opened_cursor`` ascending (AD-28).

    Story 3.2 -- opportunistically sweeps expired items FIRST (I/O matrix:
    "sweep runs first ... THEN the open-set SELECT excludes the now-expired
    rows"), so a caller never sees a stale item that has already passed its
    ``expires_at`` just because the seeded job hasn't ticked yet. Best-effort:
    a sweep failure is logged and swallowed, never propagated -- this read
    must never break because maintenance failed (I/O matrix: "sweep failure
    never breaks the read").
    """
    # 1. ENTRY
    log.journal.debug("[journal] needs_you.open_items: entry")
    # 2. DECISION -- best-effort sweep; failure here is maintenance falling
    # behind, not a reason to fail a read.
    try:
        await sweep_expired_items(db_pool)
    except Exception as exc:  # noqa: BLE001 -- see docstring: never break the read
        log.journal.warning(
            "[journal] needs_you.open_items: opportunistic sweep failed -- "
            "continuing with the read anyway",
            exc_info=exc,
        )
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
