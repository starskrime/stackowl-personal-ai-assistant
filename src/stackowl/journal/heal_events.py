"""Attrs models for the health sweep's heal -> verify cycle, and their
registration (Story 2.6).

One emitting process: ``scheduler.handlers.health_sweep`` -- the ADR-6 heal
step (``_heal_and_verify``) is the only place a heal is attempted, and the
sweep's ``execute()`` is the only place that re-verifies and decides
attempted/healed/exhausted, both inside the same
``async with self._db.transaction()`` block as the ``heal_attempts`` row they
describe (AD-24). Importing this module registers all three types as a side
effect; ``journal/__init__.py`` imports it for exactly that reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict

from stackowl.journal.enums import AttentionClass, Intensity, NeedsYouKind, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.records import (
    ExpiredRecord,
    get_record_reader_registry,
    read_sqlite_record,
    refuse_unless_owner,
)
from stackowl.journal.registry import EventTypeSpec, get_registry

if TYPE_CHECKING:  # pragma: no cover -- typing-only
    from stackowl.db.pool import DbPool

_EMITTING_PROCESS = "scheduler.handlers.health_sweep"
_TABLE = "heal_attempts"


class HealAttemptedAttrs(JournalAttrsBase):
    """``heal.attempted`` -- the sweep called ``ensure_available()`` on an
    unhealthy subsystem's registered ``HealableResource``."""

    attempt_count: int


class HealHealedAttrs(JournalAttrsBase):
    """``heal.healed`` -- the sweep's re-collect, AFTER the attempt above,
    found the subsystem healthy again."""

    attempt_count: int


class HealExhaustedAttrs(JournalAttrsBase):
    """``heal.exhausted`` -- the sweep attempted a heal and the subsystem is
    STILL unhealthy after its own re-verify (``attempted - healed``). The
    healer's give-up, exactly what AD-5 says must reach the owner."""

    attempt_count: int


def _narrate_attempted(attrs: JournalAttrsBase, name: str) -> str:
    return f"Healing {name} was attempted."


def _narrate_healed(attrs: JournalAttrsBase, name: str) -> str:
    return f"{name} recovered."


def _narrate_exhausted(attrs: JournalAttrsBase, name: str) -> str:
    from typing import cast

    exhausted = cast(HealExhaustedAttrs, attrs)
    return f"Healing {name} gave up after {exhausted.attempt_count} attempt(s)."


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="heal.attempted", schema_version=1, attrs_model=HealAttemptedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.HEAL,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_attempted,
    ))
    registry.register(EventTypeSpec(
        type="heal.healed", schema_version=1, attrs_model=HealHealedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.HEAL,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_healed,
    ))
    registry.register(EventTypeSpec(
        type="heal.exhausted", schema_version=1, attrs_model=HealExhaustedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.HEAL,
        # AD-5, verbatim: heal.exhausted is one of the two NAMED needs_you/high
        # examples -- the heal loop stopped trying.
        attention_class=AttentionClass.NEEDS_YOU, intensity=Intensity.HIGH,
        # Story 3.1 (AD-28) -- a give-up opens a durable `incident` item.
        needs_you_kind=NeedsYouKind.INCIDENT,
        table=_TABLE, narrate=_narrate_exhausted,
    ))


_register()


class HealAttemptRecordView(BaseModel):
    """Typed view of one ``heal_attempts`` row -- the registered reader's
    return shape for ``RecordKind.HEAL``/``sqlite`` (AD-4)."""

    model_config = ConfigDict(frozen=True)

    id: str
    subsystem: str
    status: str
    attempt_count: int
    created_at: str
    updated_at: str


async def read_heal_attempt_record(
    db_pool: DbPool | None, locator: dict[str, str], *, owner_id: str,
) -> HealAttemptRecordView | ExpiredRecord:
    """The registered reader for ``RecordKind.HEAL``/``sqlite`` (AD-4): opens
    the ``heal_attempts`` row a ``heal.*`` event's ``record_ref`` points at."""
    refuse_unless_owner(RecordKind.HEAL, owner_id)
    row = await read_sqlite_record(
        db_pool, table=_TABLE, id_column="id",
        id_value=locator.get("id", ""), view_model=HealAttemptRecordView,
    )
    if row is None:
        return ExpiredRecord(
            record_kind=RecordKind.HEAL, locator=locator,
            reason="the heal_attempts row this event referenced is gone",
        )
    return cast(HealAttemptRecordView, row)


get_record_reader_registry().register(RecordKind.HEAL, "sqlite", read_heal_attempt_record)
