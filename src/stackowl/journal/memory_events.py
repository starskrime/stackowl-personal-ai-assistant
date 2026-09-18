"""Attrs models for ``memory.written`` and ``memory.reflection_recorded``,
their registration, and the ONE recording helper ``memory.written`` needs
(Story 2.8).

WHY THIS MODULE IS SHAPED LIKE ``turn_events.py``, NOT LIKE
``task_events.py``/``job_events.py``/``heal_events.py``/``health_events.py``.
Those four are registration-only: the subsystem that owns the state change
already opens its own ``async with self._db.transaction() as conn:`` block for
that change, and calls ``journal.record(conn, ...)`` directly inside it.

``CuratedMemory._write()`` has NO such transaction to join -- curated memory
lives in md files, not SQLite (the Boundaries contract: "own fresh
transaction — no existing DB mutation to piggyback on") -- so this module owns
:func:`record_md_memory_write`, which opens its own ``DbPool.transaction()``
purely to call ``journal.record()`` inside a commit (AD-24's "state outside
SQLite... records immediately after its write").

``memory.reflection_recorded`` is the OPPOSITE shape: ``ReflectionWriterHandler``
already opens its own chunk transaction around ``ReflectionStore.write()``, so
THAT call site calls ``journal.record()`` directly on its own already-open
``tx`` -- no helper here, only the attrs model and registration. This module
still registers it (and journal/__init__.py imports this module for exactly
that registration side effect), mirroring how ``task_events.py`` registers
types whose recording lives entirely at the caller's own transaction site.

Importing this module registers both types as a side effect;
``journal/__init__.py`` imports it for exactly that reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import Field

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.journal.enums import ActorKind, AttentionClass, Outcome, RecordKind
from stackowl.journal.models import JournalAttrsBase, JournalEvent, RecordRef
from stackowl.journal.recorder import record as journal_record
from stackowl.journal.registry import EventTypeSpec, get_registry

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    from pathlib import Path

    from stackowl.db.pool import DbPool

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- the one bound every open string field below
#: enforces, mirroring every other journal attrs module's own constant.
_MAX_LABEL_LEN = 64

#: Mirrors ``memory/curated.py``'s own closed operation vocabulary -- COPIED,
#: not imported: ``journal/`` imports nothing from any subsystem (AD-7). Kept
#: in sync by hand, the same "reused structure, not reused values" shape
#: ``journal/turn_events.py::_ActionSeverity`` already documents.
_MemoryOp = Literal["add", "replace", "remove"]

#: The curated-memory target this story treats as the OWNER's own profile
#: (``memory/curated.py::USER_TARGET``, mirrored as a literal for the same
#: AD-7 reason as ``_MemoryOp`` above). Anything else is an owl's own notes.
_USER_TARGET = "user"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class MemoryWrittenAttrs(JournalAttrsBase):
    """``memory.written`` -- one curated md write completed
    (``CuratedMemory._write``, called from ``add``/``replace``/``remove``)."""

    target: str = Field(max_length=_MAX_LABEL_LEN)
    op: _MemoryOp
    durability: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


class ReflectionRecordedAttrs(JournalAttrsBase):
    """``memory.reflection_recorded`` -- one ``reflections`` row committed
    (``ReflectionWriterHandler.execute``'s chunk loop)."""

    owl_name: str = Field(max_length=_MAX_LABEL_LEN)
    failure_class: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    quality_score: float | None = None


def _narrate_memory_written(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.MEMORY (out of scope)
    a = cast(MemoryWrittenAttrs, attrs)
    verb = {"add": "added to", "replace": "replaced in", "remove": "removed from"}[a.op]
    return f"An entry was {verb} {a.target}'s memory."


def _narrate_reflection_recorded(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001
    a = cast(ReflectionRecordedAttrs, attrs)
    if a.failure_class:
        return f"A reflection was recorded for {a.owl_name} ({a.failure_class})."
    return f"A reflection was recorded for {a.owl_name}."


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="memory.written", schema_version=1, attrs_model=MemoryWrittenAttrs,
        emitting_process="memory.curated", record_kind=RecordKind.MEMORY,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        narrate=_narrate_memory_written,
    ))
    registry.register(EventTypeSpec(
        type="memory.reflection_recorded", schema_version=1,
        attrs_model=ReflectionRecordedAttrs,
        emitting_process="memory.reflection_writer_handler", record_kind=RecordKind.MEMORY,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        narrate=_narrate_reflection_recorded,
    ))


_register()


def _target_kind(target: str) -> ActorKind:
    """The OWNER's own profile vs. an owl's own notes (AD-3's one closed list,
    doubling as ``target_kind``): ``user`` is the owner's global profile,
    anything else is the routing name of an owl's own ``<owl>.md``."""
    return ActorKind.OWNER if target == _USER_TARGET else ActorKind.OWL


async def record_md_memory_write(
    db_pool: DbPool | None,
    *,
    target: str,
    path: Path,
    op: _MemoryOp,
    changed_index: int | None,
    durability: str | None,
    actor_kind: ActorKind,
    actor_id: str,
) -> None:
    """Record one ``memory.written`` event, in its OWN fresh transaction
    (AD-24: "state outside SQLite... records immediately after its write").

    Called from ``CuratedMemory._write`` immediately after the atomic
    temp-file-replace lands. Never raises (B5): a failure here must never
    undo the completed file write or cost the caller its ``MemoryResult`` --
    any failure (no ``db_pool``, the transaction, ``journal.record()``
    itself) is logged at ERROR and swallowed; ``journal.record()``'s own
    failure path (recorder.py) already degrades
    :class:`~stackowl.journal.health.JournalHealthContributor` on its own, so
    this only needs to degrade health for a failure that happens BEFORE that
    call ever runs.

    ``changed_index`` becomes ``record_ref.locator["anchor"]`` as
    ``f"entry-{changed_index}"`` when given -- ``add``/``replace`` pass the
    entry's post-write index; ``remove`` passes ``None`` (a removed entry has
    no post-write position, so the locator carries ``path`` only).

    A no-op (file write still stands, nothing journaled) when ``db_pool`` is
    ``None`` -- mirrors ``memory/curated.py::shared_memory()``'s own
    module-global "writes are stateless" pattern: a test or standalone
    construction that never called ``curated.set_db_pool()`` must not raise.
    """
    # 1. ENTRY
    log.memory.debug(
        "[journal] memory_events.record_md_memory_write: entry",
        extra={"_fields": {"target": target, "op": op}},
    )
    # 2. DECISION -- no DbPool wired (test / standalone construction) is a
    # silent no-op.
    if db_pool is None:
        log.memory.debug(
            "[journal] memory_events.record_md_memory_write: exit -- no db_pool wired",
            extra={"_fields": {"target": target, "op": op}},
        )
        return
    try:
        from stackowl.paths import StackowlHome

        occurred_at = _now_iso()
        # AD-4's `md` record_ref: a StackowlHome-relative path (+ a section
        # anchor when a specific entry has a stable post-write position).
        try:
            rel_path = str(path.relative_to(StackowlHome.home()))
        except ValueError:
            # Defensive only -- `path_for` always builds paths under
            # `memory_dir()`, itself under the home. A future caller passing
            # an out-of-home path must not crash the write; the locator
            # degrades to the absolute path rather than losing the row.
            rel_path = str(path)
        locator: dict[str, str] = {"path": rel_path}
        if changed_index is not None:
            locator["anchor"] = f"entry-{changed_index}"
        trace_id = TraceContext.get().get("trace_id")
        async with db_pool.transaction() as conn:
            await journal_record(conn, JournalEvent(
                type="memory.written", schema_version=1, occurred_at=occurred_at,
                actor_kind=actor_kind, actor_id=actor_id,
                target_kind=_target_kind(target), target_id=target, outcome=Outcome.OK,
                record_ref=RecordRef(kind="md", locator=locator),
                attrs=MemoryWrittenAttrs(target=target, op=op, durability=durability),
                trace_id=str(trace_id) if trace_id else None,
            ))
    except Exception as exc:
        # journal.record() already degraded health itself on its own
        # failure path (recorder.py) whenever it is the one that raised --
        # this catch exists so NEITHER that nor a path-resolution/
        # attrs-validation failure above (which never reaches recorder.py,
        # so nothing else degrades health for it) can ever propagate into
        # `CuratedMemory._write`, which has ALREADY completed the real file
        # write by the time this is called. Never double-counts recorder.py's
        # own failure streak by calling `note_failure` a second time here.
        log.memory.error(
            "[journal] memory_events.record_md_memory_write: FAILED -- the curated "
            "file write already succeeded; only its journal row is missing",
            exc_info=exc,
            extra={"_fields": {"target": target, "op": op}},
        )
        return
    # 4. EXIT
    log.memory.debug(
        "[journal] memory_events.record_md_memory_write: exit -- recorded",
        extra={"_fields": {"target": target, "op": op}},
    )
