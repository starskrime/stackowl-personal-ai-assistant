"""``budget.warning`` -- the cost tracker's daily-budget-crossed-80%-warning
signal, journaled for the first time (Story 3.1).

Registration-only, mirroring ``task_events.py``'s/``heal_events.py``'s own
shape: the emitting subsystem (``providers.cost_tracker``) already opens its
own ``async with self._db.transaction() as conn:`` block and calls
``journal.record()`` directly inside it -- no recording helper lives here.

WHY ``table=None``. ``budget.warning`` describes no single owning row: it is
a signal over an AGGREGATE (the day's running total across every
``cost_records`` row for that date), not one row a ``record_ref`` could point
at. ``EventTypeSpec.table``'s own docstring already names this exact case
(previously only ``memory.written``'s ``md``-backed reason; this is a second,
distinct reason -- no owning row at all, not a different storage backend).
"""

from __future__ import annotations

from typing import cast

from pydantic import Field

from stackowl.journal.enums import AttentionClass, Intensity, NeedsYouKind, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventTypeSpec, get_registry

_EMITTING_PROCESS = "providers.cost_tracker"

#: AD-4, verbatim: "attrs hold ids, numbers, closed enums and bounded labels
#: of at most 64 characters" -- mirrors every other journal attrs module's
#: own constant. `date` is an ISO-8601 date (10 chars), well under it.
_MAX_LABEL_LEN = 64


class BudgetWarningAttrs(JournalAttrsBase):
    """``budget.warning`` -- ``CostTracker._check_budget`` saw the daily
    ratio cross 80% of the configured limit for ``date``, for the first
    time that date (``_warned_dates`` dedup, unchanged by this story)."""

    date: str = Field(max_length=_MAX_LABEL_LEN)
    current_usd: float
    limit_usd: float
    ratio_pct: int


def _narrate_warning(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001 -- no resolver registered for RecordKind.COST
    a = cast(BudgetWarningAttrs, attrs)
    return (
        f"The daily LLM budget reached {a.ratio_pct}% on {a.date}: "
        f"${a.current_usd:.2f} of ${a.limit_usd:.2f}."
    )


def _register() -> None:
    get_registry().register(EventTypeSpec(
        type="budget.warning", schema_version=1, attrs_model=BudgetWarningAttrs,
        emitting_process=_EMITTING_PROCESS, record_kind=RecordKind.COST,
        attention_class=AttentionClass.NEEDS_YOU, intensity=Intensity.NORMAL,
        needs_you_kind=NeedsYouKind.ALERT,
        # No single owning row -- a signal over the day's aggregate total,
        # never journaled before this story (spec Design Notes).
        table=None, narrate=_narrate_warning,
    ))


_register()
