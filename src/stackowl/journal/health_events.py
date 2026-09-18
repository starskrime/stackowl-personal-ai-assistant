"""Attrs model for a subsystem's health-status transition, and its
registration (Story 2.6, FR84).

One emitting process: ``scheduler.handlers.health_sweep`` -- ``execute()``'s
``_record_health_changes`` helper is the only place a subsystem's previous
and new status are compared, inside the same
``async with self._db.transaction() as conn:`` block as the
``health_status_changes`` row it inserts (AD-24). Importing this module
registers the type as a side effect; ``journal/__init__.py`` imports it for
exactly that reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field

from stackowl.journal.enums import AttentionClass, RecordKind
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
_TABLE = "health_status_changes"

#: Same bound every other journal attrs field uses (AD-4) -- `HealthState` is
#: itself a short closed literal ("ok"/"degraded"/"down"/"unknown"), well
#: inside it; the bound is declared here rather than assumed.
_MAX_LABEL_LEN = 64


class HealthChangedAttrs(JournalAttrsBase):
    """``health.changed`` -- a subsystem's status differs from the last row
    recorded for it in ``health_status_changes`` (a real transition, never a
    first observation or an unchanged tick -- the health sweep itself decides
    that before ever building this).

    ``previous_status``/``new_status`` are bounded strings from
    ``health.status.HealthState``'s closed literal. ``error_code`` is the
    closed, bounded :class:`~stackowl.journal.enums.HealthErrorCode` --
    NEVER ``str(exc)`` or any exception text (FR84, AD-4).
    """

    previous_status: str = Field(max_length=_MAX_LABEL_LEN)
    new_status: str = Field(max_length=_MAX_LABEL_LEN)
    error_code: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


def _narrate_changed(attrs: JournalAttrsBase, name: str) -> str:
    from typing import cast

    changed = cast(HealthChangedAttrs, attrs)
    return f"{name} changed from {changed.previous_status} to {changed.new_status}."


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="health.changed", schema_version=1, attrs_model=HealthChangedAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.HEALTH,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_changed,
    ))


_register()


class HealthStatusChangeRecordView(BaseModel):
    """Typed view of one ``health_status_changes`` row -- the registered
    reader's return shape for ``RecordKind.HEALTH``/``sqlite`` (AD-4)."""

    model_config = ConfigDict(frozen=True)

    id: str
    subsystem: str
    previous_status: str
    new_status: str
    error_code: str | None = None
    occurred_at: str


async def read_health_status_change_record(
    db_pool: DbPool | None, locator: dict[str, str], *, owner_id: str,
) -> HealthStatusChangeRecordView | ExpiredRecord:
    """The registered reader for ``RecordKind.HEALTH``/``sqlite`` (AD-4):
    opens the ``health_status_changes`` row a ``health.changed`` event's
    ``record_ref`` points at."""
    refuse_unless_owner(RecordKind.HEALTH, owner_id)
    row = await read_sqlite_record(
        db_pool, table=_TABLE, id_column="id",
        id_value=locator.get("id", ""), view_model=HealthStatusChangeRecordView,
    )
    if row is None:
        return ExpiredRecord(
            record_kind=RecordKind.HEALTH, locator=locator,
            reason="the health_status_changes row this event referenced is gone",
        )
    return cast(HealthStatusChangeRecordView, row)


get_record_reader_registry().register(
    RecordKind.HEALTH, "sqlite", read_health_status_change_record,
)
