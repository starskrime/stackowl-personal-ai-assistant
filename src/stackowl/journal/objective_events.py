"""``objective.set`` -- the objective lifecycle event (Story 4.7).

ONE emitting process: ``objectives`` -- ``objectives/commands.py``'s
``scheduling.set_objective`` handler is the only place an objective's
initial persistence + decomposition commits, and it records THIS event at
that call site (AD-24), mirroring ``job_events.py``'s own "one emitting
process, records at the exact call site the state change commits" shape.
Importing this module registers the type as a side effect;
``journal/__init__.py`` imports it for exactly that reason.

``_objective_record_ref`` lives HERE rather than in the emitter
(``objectives/commands.py``) -- unlike ``job_events.py``'s own
``_job_record_ref`` (duplicated at each of ``scheduler.py``'s/
``scheduler_mutations.py``'s TWO emitting call sites, per that file's own
documented precedent), ``objective.set`` has exactly ONE emitting call site,
so there is nothing to keep from drifting by duplication -- one home is
simplest.
"""

from __future__ import annotations

from pydantic import Field

from stackowl.journal.enums import AttentionClass, RecordKind
from stackowl.journal.models import JournalAttrsBase, RecordRef
from stackowl.journal.registry import EventTypeSpec, get_registry

_EMITTING_PROCESS = "objectives"
_TABLE = "objectives"

#: AD-4's own bound, mirrors ``job_events.py``'s own module-level constant.
_MAX_LABEL_LEN = 64


class ObjectiveSetAttrs(JournalAttrsBase):
    """``objective.set`` -- Story 4.7: ``scheduling.set_objective`` committed
    (an objective's initial persistence + decomposition), driven through a
    COMMAND task."""

    command_id: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)


def _narrate_set(attrs: JournalAttrsBase, name: str) -> str:
    return f"Objective {name} created."


def _register() -> None:
    registry = get_registry()
    registry.register(EventTypeSpec(
        type="objective.set", schema_version=1, attrs_model=ObjectiveSetAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.OBJECTIVE,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        table=_TABLE, narrate=_narrate_set,
    ))


_register()


def _objective_record_ref(objective_id: str) -> RecordRef:
    """AD-4's record_ref for the ``objective.set`` event -- always
    ``objectives``, keyed by ``objective_id``."""
    return RecordRef(kind="sqlite", locator={"table": _TABLE, "objective_id": objective_id})
