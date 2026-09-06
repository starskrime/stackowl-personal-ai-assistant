"""The cadence check counts empty stores. It never said WHICH.

WHY THIS EXISTS, and why the bigger guard was deliberately NOT built.

`silent_stores` skips a store whose clock reads NULL with the comment
``# empty table: a QUESTION, not an answer. Not this check's.`` Measured
2026-09-06: **no other check owns that question.** `cadence_report` is the only
consumer of the registry, and the deferral points at nothing.

THE OBVIOUS FIX WAS MEASURED AND REJECTED. "Report a never-written store whose
cadence expects writes" sounds right, and against this deployment its entire
population would be exemptions:

  * 14 declared stores are empty. THREE are RETIRED (expected empty). EIGHT are
    ON_DEMAND and one is SEED — all with `max_silence_days = None`, so they are
    skipped at the FIRST guard, not the empty-table one.
  * That leaves exactly TWO with a cadence and a clock, and both are explained:
    `cache_breakpoint_probes` records only positives and this deployment reports
    no cache stats at all (operator decision, ESC-149); `notification_queue` is
    written only when a notification is BATCHED rather than delivered, and focus
    mode has never been engaged.

A guard whose every finding is an exemption is decoration that cries wolf on
correct work — the failure this repo pays for most often. So the skip stays.

WHAT IS ACTUALLY WRONG IS SMALLER AND REAL. `CadenceReport` already carries
`empty` as a COUNT, and the ok-line already prints it, because "no store is
silent" is worthless without its denominator. But a count cannot be acted on: to
learn WHICH stores were skipped, someone must re-derive the whole registry
against the live database by hand, which is exactly what this session did. The
data is in the function already; it was thrown away one line before it could be
reported.

So the fix is to name them. It raises no alarm, adds no exemption list, and
turns the question the comment defers into one a reader can actually ask.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.health.store_cadence import DECLARATIONS, cadence_report

pytestmark = pytest.mark.asyncio


class _Db:
    """A db whose every declared clock reads NULL — i.e. every store empty."""

    def __init__(self, non_empty: dict[str, str] | None = None) -> None:
        self._non_empty = non_empty or {}

    async def fetch_all(self, sql: str, _params: Any = ()) -> list[dict[str, Any]]:
        for table, value in self._non_empty.items():
            if f"FROM {table}" in sql:
                return [{"t": value}]
        return [{"t": None}]


async def test_the_report_names_the_empty_stores_not_only_their_count() -> None:
    """THE DEFECT ITSELF. `empty=2` cannot be acted on; `empty_tables=(...)` can."""
    report = await cadence_report(_Db())

    assert report.empty > 0, "fixture built no empty stores — the test proves nothing"
    assert hasattr(report, "empty_tables"), (
        "the report counts empty stores but still cannot say which ones"
    )
    assert len(report.empty_tables) == report.empty, (
        f"the names and the count disagree: {len(report.empty_tables)} vs {report.empty}"
    )


async def test_it_names_only_stores_the_check_actually_looked_at() -> None:
    """A store with no cadence limit or no clock is skipped BEFORE the empty
    test, so naming it would report something never measured — the instrument
    lying in the other direction."""
    report = await cadence_report(_Db())
    considered = {
        d.table for d in DECLARATIONS
        if d.cadence.max_silence_days is not None and d.clock is not None
    }

    assert set(report.empty_tables) <= considered, (
        f"named stores the check never examined: {set(report.empty_tables) - considered}"
    )


async def test_a_written_store_is_not_named_as_empty() -> None:
    """The control in the other direction: a store WITH a readable clock must
    leave the empty list, or the names are just the registry restated."""
    target = next(
        d for d in DECLARATIONS
        if d.cadence.max_silence_days is not None and d.clock is not None
    )
    report = await cadence_report(_Db(non_empty={target.table: "2026-09-06T12:00:00+00:00"}))

    assert target.table not in report.empty_tables
    assert report.measured >= 1


async def test_the_empty_list_is_stable_and_sorted() -> None:
    """A set rendered into a log line must not reorder between runs — a reader
    diffing two sweeps would see churn that is not there."""
    report = await cadence_report(_Db())

    assert list(report.empty_tables) == sorted(report.empty_tables)
