""""No timestamp column" was false for every store that claimed it.

``store_cadence`` exists because "a store quietly outside the check is exactly
the blind spot this registry exists to remove" — its own words. It then built one
category that puts a store outside the check, ``UNMEASURABLE``, defined as::

    UNMEASURABLE = (None, "no timestamp column; cannot be measured")

MEASURED 2026-09-03 against the live schema. Fifteen stores were declared
UNMEASURABLE. All fifteen have an ``_at`` column::

    job_results              run_at                      newest 2026-09-03
    staged_facts             staged_at                   newest 2026-09-03
    channel_liveness         last_receive_at             newest 2026-09-03
    callback_log             processed_at                newest 2026-09-03
    contradiction_scan_state last_contradiction_scan_at  newest 2026-08-10
    dna_checkpoints          created_at                  newest 2026-07-14
    command_sequence_last    updated_at                  newest 2026-05-23
    ... and eight more with a clock and zero rows

So the hatch was not "these cannot be measured". It was "these are not measured",
on a premise nobody checked against the schema.

WHAT WAS HIDING IN IT. Three of the fifteen have ZERO references anywhere in
``src/`` — ``reindex_queue``, ``langgraph_checkpoints`` and
``contradiction_scan_state``. That is the same measure by which ``job_queue`` was
deleted, and it is the exact class of finding this module was BUILT to produce:
its docstring reports that its first run found ``dreamworker_runs`` and
``kuzu_sync_log`` with zero references, "two tables outliving the features that
wrote them by weeks". ``contradiction_scan_state`` even carries the evidence —
its watermark reads 2026-08-10T19:15:12, the timestamp of the last
``memory.contradiction`` row in the audit log, frozen when migration 0112
retired the fact store its scanner read.

THE CAUSE IS ONE HALF OF A RULE MADE EXECUTABLE AND THE OTHER HALF REMEMBERED.
``test_every_store_declares_its_cadence`` already fails when the schema holds a
table the registry does not name — the module says out loud that "a
hand-maintained list rots the first time someone adds a table and forgets", and
made THAT half a test. Nothing checked that a declaration is TRUE. So the
completeness half could not rot and the correctness half rotted silently.

WHAT CHANGES. ``UNMEASURABLE`` now has to earn itself: a table declaring it must
genuinely have no ``_at`` column, asserted against the live schema. A store whose
WRITER was removed gets ``RETIRED``, which names the state instead of pretending
the store cannot be read — and four stores move out of the hatch into real
measurement, so ``staged_facts`` and ``channel_liveness`` can now alarm within a
day if their loops stop, which they could not do before.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stackowl.health.store_cadence import DECLARATIONS, Cadence

#: The three with zero references anywhere in src/ — measured, not assumed.
ORPHANED = ("reindex_queue", "langgraph_checkpoints", "contradiction_scan_state")

#: Moved out of the hatch into real measurement by this change.
NOW_MEASURED = ("job_results", "staged_facts", "channel_liveness", "callback_log")


def _schema_sql() -> str:
    root = Path(__file__).resolve().parents[2] / "src" / "stackowl" / "db" / "migrations"
    return "\n".join(p.read_text(errors="ignore") for p in sorted(root.glob("*.sql")))


def _columns_for(table: str, sql: str) -> set[str]:
    """Every column name the migrations ever give ``table`` (CREATE + ALTER)."""
    cols: set[str] = set()
    for m in re.finditer(
        rf"CREATE TABLE (?:IF NOT EXISTS )?{table}\s*\((.*?)\n\s*\);", sql, re.S | re.I,
    ):
        cols |= set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\s", m.group(1), re.M | re.I))
    cols |= set(re.findall(
        rf"ALTER TABLE {table} ADD COLUMN\s+([a-z_][a-z0-9_]*)", sql, re.I,
    ))
    return {c.lower() for c in cols}


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_unmeasurable_means_what_it_says() -> None:
    """THE ROOT CAUSE, made executable. The completeness half of this registry's
    rule was already a test; the correctness half was a comment, and it was false
    for all fifteen entries that used it. A store declaring "no timestamp column"
    must genuinely have none."""
    sql = _schema_sql()
    liars = []
    for decl in DECLARATIONS:
        if decl.cadence is not Cadence.UNMEASURABLE:
            continue
        clocks = sorted(c for c in _columns_for(decl.table, sql) if c.endswith("_at"))
        if clocks:
            liars.append(f"{decl.table} declares no clock but has {clocks}")
    assert not liars, (
        "a store is exempt from the silence check on a premise the schema "
        "contradicts:\n  " + "\n  ".join(liars)
    )


def test_a_store_whose_writer_was_removed_says_so() -> None:
    """RETIRED names the state. Filing a writer-less store as "cannot be
    measured" is not merely inaccurate — it is the sentence that kept three
    zero-reference tables out of the one check built to find them."""
    assert Cadence.RETIRED.max_silence_days is None, (
        "a retired store must never alarm — its silence is the design"
    )
    assert "writer" in Cadence.RETIRED.why.lower()


@pytest.mark.parametrize("table", ORPHANED)
def test_the_orphaned_stores_are_declared_retired(table: str) -> None:
    """Measured: zero references anywhere in src/ — the same standard by which
    job_queue was deleted."""
    decl = next(d for d in DECLARATIONS if d.table == table)
    assert decl.cadence is Cadence.RETIRED, f"{table} is {decl.cadence}"
    assert decl.note, f"{table} must say WHY it is retired"


@pytest.mark.parametrize("table", NOW_MEASURED)
def test_the_live_stores_are_actually_measured_now(table: str) -> None:
    """The point of the change, not a side effect. These four are written daily
    and were exempt; now a stopped loop shows up as silence."""
    decl = next(d for d in DECLARATIONS if d.table == table)
    assert decl.clock, f"{table} still declares no clock"
    assert decl.cadence is not Cadence.UNMEASURABLE


def test_every_declaration_with_a_limit_has_a_clock_to_measure() -> None:
    """A cadence that alarms needs something to read. A limit with no clock is a
    check that can never fire — the silent-pass shape this registry is about."""
    for decl in DECLARATIONS:
        if decl.cadence.max_silence_days is not None:
            assert decl.clock, f"{decl.table} declares a limit but no clock column"


def test_no_declaration_names_a_clock_the_schema_does_not_have() -> None:
    """The other direction of the same guard: a declared clock that does not
    exist makes ``silent_stores`` log 'could not read a store's clock' and skip —
    an instrument failure that reads exactly like a healthy store."""
    sql = _schema_sql()
    wrong = []
    for decl in DECLARATIONS:
        if not decl.clock:
            continue
        cols = _columns_for(decl.table, sql)
        if cols and decl.clock.lower() not in cols:
            wrong.append(f"{decl.table}.{decl.clock} is not in the schema")
    assert not wrong, "\n  ".join(wrong)
