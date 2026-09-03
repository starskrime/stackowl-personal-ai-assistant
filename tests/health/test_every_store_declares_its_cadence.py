"""A table in the schema that declares no cadence is a blind spot.

WHY THIS IS A TEST AND NOT A CONVENTION. The registry it guards exists because
Bakir rejected two data-derived rules for detecting a stopped store: ``owls`` at
11.8 days idle is correct, ``retry_queue`` at 5.9 days is a stopped engine, and
the data looks identical. Only what the store is FOR tells them apart, so the
purpose has to be declared.

A hand-maintained list of declarations rots the first time someone adds a table
and forgets — and then the one store nobody declared is invisible to the check
built to find exactly that. So the list is not maintained by memory. This test
compares it against the real migrated schema and fails on the first gap in either
direction.

BOTH DIRECTIONS MATTER. An undeclared table is an unmonitored store. A declared
table that no longer exists is residue of a deletion, and this repo has paid for
that shape twice in three days — stale allowlist entries for six deleted modules,
and 231 compiled modules whose source was gone.
"""

from __future__ import annotations

import pytest

from stackowl.health.store_cadence import DECLARATIONS, Cadence, declaration_for

#: Views, virtual tables and FTS shadow tables are not stores.
_NOT_A_STORE = ("sqlite_", "_fts")


def _schema_tables(db_path) -> set[str]:  # noqa: ANN001
    import sqlite3

    con = sqlite3.connect(str(db_path))
    try:
        return {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if not any(s in r[0] for s in _NOT_A_STORE)
        }
    finally:
        con.close()


@pytest.mark.tripwire
def test_every_table_in_the_schema_declares_a_cadence(tmp_path) -> None:  # noqa: ANN001
    from tests._schema_template import seed_schema

    path = tmp_path / "schema.db"
    seed_schema(path)
    undeclared = sorted(t for t in _schema_tables(path) if declaration_for(t) is None)

    assert not undeclared, (
        "these tables are in the schema and declare no write cadence, so a "
        "stopped writer on any of them is invisible:\n  "
        + "\n  ".join(undeclared)
        + "\nDeclare each in stackowl/health/store_cadence.DECLARATIONS — the "
        "class you pick IS the statement of what the store is for."
    )


@pytest.mark.tripwire
def test_no_declaration_outlives_its_table(tmp_path) -> None:  # noqa: ANN001
    """The other direction: a declaration for a table that no longer exists.

    Residue of a deletion, and it would make the registry read as covering more
    than it does. Same shape as the six stale owner-scope allowlist entries and
    the 231 orphaned .pyc files, both found in the same week."""
    from tests._schema_template import seed_schema

    path = tmp_path / "schema.db"
    seed_schema(path)
    real = _schema_tables(path)
    ghosts = sorted(d.table for d in DECLARATIONS if d.table not in real)

    assert not ghosts, (
        "these tables are declared but are not in the schema:\n  "
        + "\n  ".join(ghosts)
    )


def test_a_measurable_class_must_carry_a_clock() -> None:
    """HOT and PERIODIC promise a measurement. Without a clock column the
    promise is silently unkept — the store looks covered and is not."""
    broken = [
        d.table for d in DECLARATIONS
        if d.cadence in (Cadence.HOT, Cadence.PERIODIC) and not d.clock
    ]
    assert not broken, broken


def test_UNMEASURABLE_is_the_only_class_that_may_lack_a_clock() -> None:
    """The control for the test above. A store with no clock must say so through
    the class, not by leaving a field empty in a class that implies one."""
    mislabelled = [
        d.table for d in DECLARATIONS
        if d.cadence is Cadence.UNMEASURABLE and d.clock
    ]
    assert not mislabelled, mislabelled


# --------------------------------------------------------------------------- #
# The check itself — and the bug that made its first answer meaningless.
# --------------------------------------------------------------------------- #


class _Db:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values
        self.asked: list[str] = []

    async def fetch_all(self, sql: str, params: tuple) -> list[dict]:
        table = sql.rsplit(" FROM ", 1)[1].strip()
        self.asked.append(table)
        return [{"t": self.values.get(table)}]


@pytest.mark.asyncio
async def test_an_ISO_STRING_clock_is_measured_not_silently_skipped() -> None:
    """The bug that made the first live run's "no store is silent" meaningless.

    This database stores timestamps in TWO formats: ``task_outcomes.captured_at``
    is an epoch float, ``tasks.created_at`` is an ISO-8601 string. A bare
    ``float(raw)`` raises on the second, and the first cut caught that and moved
    on — so the check reported a clean zero having silently skipped most of the
    stores it claimed to cover. Caught by asking what the zero was MADE OF: 26
    stores are measurable, and the answer had been built from almost none of them.
    """
    from datetime import UTC, datetime

    from stackowl.health.store_cadence import silent_stores

    now = 1_800_000_000.0
    long_ago = datetime.fromtimestamp(now - 30 * 86400, UTC).isoformat()
    db = _Db({"tasks": long_ago})

    silent = await silent_stores(db, now=now)

    assert [s.table for s in silent] == ["tasks"]
    assert silent[0].idle_days == pytest.approx(30.0, abs=0.1)


@pytest.mark.asyncio
async def test_an_EPOCH_clock_is_measured_too() -> None:
    """The control: fixing the string format must not break the float one."""
    from stackowl.health.store_cadence import silent_stores

    now = 1_800_000_000.0
    db = _Db({"task_outcomes": now - 30 * 86400})

    assert [s.table for s in await silent_stores(db, now=now)] == ["task_outcomes"]


@pytest.mark.asyncio
async def test_a_store_INSIDE_its_declared_cadence_is_not_reported() -> None:
    from stackowl.health.store_cadence import silent_stores

    now = 1_800_000_000.0
    # retry_queue declares 7 days and sat at 5.9 on the live database — the real
    # margin this check is currently running against.
    db = _Db({"retry_queue": now - 5.9 * 86400})

    assert await silent_stores(db, now=now) == []


@pytest.mark.asyncio
async def test_an_ON_DEMAND_store_is_NEVER_reported_however_quiet() -> None:
    """``owls`` at 11.8 days idle is CORRECT — he has not created an owl. This is
    the case that refuted both data-derived rules and forced the registry."""
    from stackowl.health.store_cadence import silent_stores

    now = 1_800_000_000.0
    db = _Db({"owls": now - 400 * 86400})

    assert await silent_stores(db, now=now) == []
    assert "owls" not in db.asked, (
        "an ON_DEMAND store was queried at all — it can never alarm, so reading "
        "it is pure cost on every sweep"
    )


@pytest.mark.asyncio
async def test_an_unreadable_store_does_not_hide_the_others() -> None:
    """One broken table must not turn the whole check into a clean zero."""
    from stackowl.health.store_cadence import silent_stores

    now = 1_800_000_000.0

    class _Partial(_Db):
        async def fetch_all(self, sql: str, params: tuple) -> list[dict]:
            if " FROM jobs" in sql:
                raise RuntimeError("no such table")
            return await super().fetch_all(sql, params)

    db = _Partial({"tasks": now - 30 * 86400})
    assert [s.table for s in await silent_stores(db, now=now)] == ["tasks"]


@pytest.mark.asyncio
async def test_a_HEALTHY_report_says_what_it_actually_LOOKED_AT() -> None:
    """"No store is silent" is worthless without "out of how many".

    This check's own first live run returned a clean zero while a date-format bug
    had silently skipped almost every store it claimed to cover. A healthy report
    that cannot state its denominator is that same trap with a tick beside it.
    """
    from stackowl.health.store_cadence import cadence_report

    now = 1_800_000_000.0
    db = _Db({"tasks": now - 3600, "task_outcomes": now - 3600})

    report = await cadence_report(db, now=now)

    assert report.silent == ()
    assert report.measured == 2
    assert report.empty > 0, (
        "every other declared store returned None here, so a zero `empty` would "
        "mean the counter is not counting"
    )
