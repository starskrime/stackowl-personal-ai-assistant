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

from pydantic import Field

from stackowl.journal.enums import AttentionClass, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventTypeSpec, get_registry

_EMITTING_PROCESS = "scheduler.handlers.health_sweep"

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
        narrate=_narrate_changed,
    ))


_register()
