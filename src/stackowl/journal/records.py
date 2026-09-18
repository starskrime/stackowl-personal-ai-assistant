"""``RecordReaderRegistry`` -- one registered reader per ``(RecordKind,
carrier)`` pair (AD-4, Story 2.10): "Each record kind has one registered
reader supplied by the owning subsystem, typed and authority-checked."

PLACEMENT: inside ``journal/`` itself, alongside ``registry.py`` and
``narrator.py`` -- the record-reader registry is explicitly named in AD-7's
own package inventory ("record-reader registry, narrator ... every subsystem
may import it. It imports nothing from ``bridge/``, ``voice/`` or any
subsystem"). This module therefore imports nothing from ``tenancy/`` either,
even though the authority check below is exactly what ``tenancy``'s
``DEFAULT_PRINCIPAL_ID`` names -- ``_DEFAULT_PRINCIPAL_ID`` is COPIED, not
imported, mirroring ``turn_events.py::_ActionSeverity``'s own "reused
structure, not reused values" rule for the identical AD-7 reason (and
``db/migrations/0045_durable_tasks.sql``'s own comment: "The literal
'principal-default' MUST equal tenancy.principal.DEFAULT_PRINCIPAL_ID
forever").

SHAPE: mirrors ``narrator.py::register_name_resolver``/``_resolvers`` exactly
-- a module-level dict guarded by a ``threading.RLock``, ``register()``
refuses a duplicate key rather than silently replacing it (one reader per
``(RecordKind, carrier)``, the same "one type, one declaration" discipline
``registry.py::EventRegistry.register()`` already enforces for event types),
and ``reset_record_readers_for_tests()`` gives tests the same per-test-reset
escape hatch ``journal/health.py``/``narrator.py`` already have. Packaged as
a class (:class:`RecordReaderRegistry`) rather than bare module functions --
the same shape ``registry.py::EventRegistry`` itself already takes for an
identical dict+lock+register/get contract.

``(RecordKind, carrier)`` rather than ``RecordKind`` alone: AD-4's "each
record kind has one registered reader" reads naturally as the
``RecordRef.kind`` carrier (``sqlite``/``md``/``graph``), but "supplied by
the owning subsystem" is ``journal.enums.RecordKind``. ``memory`` is the one
domain that needs both (``memory.written`` is ``md``,
``memory.reflection_recorded`` is ``sqlite``) -- keying by the pair resolves
the ambiguity without inventing two competing registries (spec Design
Notes).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from stackowl.exceptions import JournalRecordReaderRefusedError
from stackowl.infra.observability import log
from stackowl.journal.enums import RecordKind

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    from stackowl.db.pool import DbPool

#: ``RecordRef.kind``'s own closed vocabulary (``journal/models.py``),
#: mirrored here rather than imported from a `Literal` field on a pydantic
#: model -- the same "the type IS the vocabulary" shape `narrator.py`'s own
#: `NameResolver` alias already uses for `RecordKind`.
Carrier = Literal["sqlite", "md", "graph"]

#: Copied, not imported, from ``tenancy.principal.DEFAULT_PRINCIPAL_ID`` --
#: see this module's own docstring for why (AD-7). This platform runs one
#: owner; the honest, minimal authority check a reader can make today is
#: refusing any caller that is not that one owner, loudly (DW-25/DW-26's own
#: reasoning, spec Design Notes).
_DEFAULT_PRINCIPAL_ID = "principal-default"


class ExpiredRecord(BaseModel):
    """What a reader returns for a locator whose target is gone (AD-4: "A
    target that is gone anyway opens as an `expired` record, never an
    error"). Never raised, never `None` -- a third, explicit outcome
    alongside "found" and "refused".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_kind: RecordKind
    locator: dict[str, str]
    reason: str


class RecordReader(Protocol):
    """One ``(RecordKind, carrier)``'s registered reader: typed,
    authority-checked, and never raising for a merely-missing target (it
    returns :class:`ExpiredRecord` instead). It MAY raise for a caller that
    fails the authority check (:class:`~stackowl.exceptions.JournalRecordReaderRefusedError`)
    -- that is a refusal, not a "the row is gone" outcome, and must be loud.
    """

    async def __call__(
        self, db_pool: DbPool | None, locator: dict[str, str], *, owner_id: str,
    ) -> BaseModel | ExpiredRecord: ...


class RecordReaderRegistry:
    """Process-wide registry of one reader per ``(RecordKind, carrier)``."""

    def __init__(self) -> None:
        self._readers: dict[tuple[RecordKind, Carrier], RecordReader] = {}
        self._lock = threading.RLock()

    def register(self, record_kind: RecordKind, carrier: Carrier, reader: RecordReader) -> None:
        """Register the one reader for ``(record_kind, carrier)``. Raises
        ``ValueError`` on a duplicate registration -- mirrors
        ``narrator.py::register_name_resolver``'s duplicate refusal exactly:
        one reader per key, never silently replaced."""
        # 1. ENTRY
        log.journal.debug(
            "[journal] RecordReaderRegistry.register: entry",
            extra={"_fields": {"record_kind": record_kind.value, "carrier": carrier}},
        )
        with self._lock:
            key = (record_kind, carrier)
            # 2. DECISION -- refuse a duplicate registration for this key.
            if key in self._readers:
                log.journal.error(
                    "[journal] RecordReaderRegistry.register: refused -- "
                    "already registered",
                    extra={"_fields": {"record_kind": record_kind.value, "carrier": carrier}},
                )
                raise ValueError(
                    f"journal record reader for ({record_kind.value!r}, "
                    f"{carrier!r}) is already registered -- one reader per "
                    "(RecordKind, carrier)"
                )
            # 3. STEP -- register it.
            self._readers[key] = reader
        # 4. EXIT
        log.journal.info(
            "[journal] RecordReaderRegistry.register: exit -- registered",
            extra={"_fields": {"record_kind": record_kind.value, "carrier": carrier}},
        )

    def get(self, record_kind: RecordKind, carrier: Carrier) -> RecordReader:
        """Look up the one reader for ``(record_kind, carrier)``. Raises
        ``KeyError`` when none is registered."""
        with self._lock:
            reader = self._readers.get((record_kind, carrier))
        if reader is None:
            raise KeyError(
                f"no journal record reader registered for "
                f"({record_kind.value!r}, {carrier!r})"
            )
        return reader

    def reset_for_tests(self) -> None:
        """Clear every registered reader. Test-only -- mirrors
        ``journal/narrator.py::reset_name_resolvers_for_tests``'s per-test
        reset convention."""
        with self._lock:
            self._readers.clear()


_record_readers = RecordReaderRegistry()


def get_record_reader_registry() -> RecordReaderRegistry:
    """The process-wide singleton every ``*_events.py`` module registers
    into and every future delivery surface reads from."""
    return _record_readers


def reset_record_readers_for_tests() -> None:
    """Module-level convenience mirroring ``narrator.py``'s own
    ``reset_name_resolvers_for_tests`` free function."""
    _record_readers.reset_for_tests()


def refuse_unless_owner(record_kind: RecordKind, owner_id: str) -> None:
    """The one authority check every registered reader runs first (spec
    Boundaries: "refuse (raise, never silently pass) a mismatch against
    tenancy.DEFAULT_PRINCIPAL_ID"). Raises
    :class:`~stackowl.exceptions.JournalRecordReaderRefusedError` loudly on a
    mismatch; a no-op otherwise. Shared here so all nine readers run the
    identical check rather than each re-typing the comparison.
    """
    if owner_id != _DEFAULT_PRINCIPAL_ID:
        log.journal.error(
            "[journal] records.refuse_unless_owner: refused -- owner_id is "
            "not the platform owner",
            extra={"_fields": {"record_kind": record_kind.value, "owner_id": owner_id}},
        )
        raise JournalRecordReaderRefusedError(record_kind.value, owner_id)


async def read_sqlite_record(
    db_pool: DbPool | None,
    *,
    table: str,
    id_column: str,
    id_value: str,
    view_model: type[BaseModel],
) -> BaseModel | None:
    """Fetch one row from ``table`` by ``id_column`` and construct
    ``view_model`` from it, or ``None`` when the row is gone (or no
    ``db_pool`` is wired -- a test/standalone construction, mirroring every
    recording helper's own ``db_pool is None`` no-op guard).

    Knows nothing about :class:`~stackowl.journal.enums.RecordKind` or the
    locator shape a caller's reader was given -- turning a ``None`` into its
    own typed :class:`ExpiredRecord` is each domain reader's own job, since
    only IT knows its ``RecordKind`` and the original locator.

    ``table``/``id_column`` are always caller-supplied constants (the nine
    ``read_*_record`` functions below each pass their own literal table
    name), never request-shaped input -- the same trust boundary
    ``store_cadence.py::silent_stores`` already relies on for its own
    ``f"SELECT MAX({decl.clock}) FROM {decl.table}"``.
    """
    if db_pool is None:
        return None
    rows = await db_pool.fetch_all(
        f"SELECT * FROM {table} WHERE {id_column} = ?",  # noqa: S608 -- caller-constant table/column, never request input
        (id_value,),
    )
    if not rows:
        return None
    return view_model.model_validate(dict(rows[0]))


async def read_md_record(
    *,
    locator: dict[str, str],
    view_model: type[BaseModel],
) -> BaseModel | None:
    """Open a ``md``-carrier locator's file for READ ONLY (AD-4: "SQLite and
    md readers run in the gateway, md read-only") and construct
    ``view_model`` from its whole text plus the locator's own ``anchor`` --
    never a full section-anchor extraction (spec Boundaries: "the md reader
    returns the whole file's text plus the locator's anchor as metadata").

    Returns ``None`` when the locator carries no ``path``, or the path does
    not resolve under :class:`~stackowl.paths.StackowlHome`'s home, or the
    file no longer exists -- the caller (``memory_events.py``'s own
    ``read_memory_written_record``) turns that into its typed
    :class:`ExpiredRecord`, the same split of responsibility
    :func:`read_sqlite_record` keeps.
    """
    from stackowl.paths import StackowlHome

    rel_path = locator.get("path")
    if not rel_path:
        return None
    path = (StackowlHome.home() / rel_path).resolve()
    home = StackowlHome.home().resolve()
    try:
        path.relative_to(home)
    except ValueError:
        # Defensive only: a locator's `path` is always written relative to
        # StackowlHome by `record_md_memory_write` -- a path that escapes the
        # home directory (e.g. via `..`) is never opened.
        return None
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return view_model.model_validate({"text": text, "anchor": locator.get("anchor")})
