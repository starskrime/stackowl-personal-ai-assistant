"""AD-3's coverage tripwire (Story 2.10, "Nothing escapes the journal"):
"every migration-created table is listed in the registry with its event
types, or marked ``unjournaled`` with a reason, and the tripwire diffs the
migrated table set against the registry."

``_schema_tables`` mirrors
``tests/health/test_every_store_declares_its_cadence.py::_schema_tables``
exactly: the live "migrated table set" comes from actually running every
migration and querying ``sqlite_master`` -- never from hand-parsing ``.sql``
files, so a table this check misses is a table the real schema never had a
chance to hide from it either.
"""

from __future__ import annotations

import sqlite3

import pytest

# Importing `stackowl.journal` registers every `*_events.py` type as a side
# effect -- the same reason `tests/journal/test_registry_and_leak_guard.py`
# imports the package rather than only `registry.py`.
from stackowl.journal import get_registry
from stackowl.journal.coverage import UNJOURNALED_TABLES, coverage_gap

#: Views, virtual tables and FTS shadow tables are not stores -- mirrors
#: ``store_cadence``'s own ``_NOT_A_STORE`` filter exactly.
_NOT_A_STORE = ("sqlite_", "_fts")


def _schema_tables(db_path: object) -> set[str]:
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
def test_every_live_table_is_registry_covered_or_excused(tmp_path) -> None:  # noqa: ANN001
    """AD-3's real-schema coverage assertion: every table a freshly migrated
    database actually has is in ``registry.tables_covered()`` or
    ``UNJOURNALED_TABLES`` with a written reason."""
    from tests._schema_template import seed_schema

    path = tmp_path / "schema.db"
    seed_schema(path)
    live_tables = _schema_tables(path)

    gap = coverage_gap(live_tables)

    assert not gap, (
        "these migrated tables are neither registry-covered "
        "(EventTypeSpec.table) nor excused in "
        "journal/coverage.py::UNJOURNALED_TABLES:\n  "
        + "\n  ".join(sorted(gap))
    )


@pytest.mark.tripwire
def test_no_table_is_both_covered_and_excused() -> None:
    """A table cannot be both a registered target AND an accepted gap --
    that would mean the excuse quietly outlived the coverage it once needed,
    the same "declaration that no longer matches reality" shape
    ``test_no_declaration_outlives_its_table`` catches for
    ``store_cadence.py``."""
    covered = get_registry().tables_covered()
    double_booked = covered & UNJOURNALED_TABLES.keys()

    assert not double_booked, (
        "these tables are BOTH registry-covered and excused as "
        "unjournaled -- remove the stale UNJOURNALED_TABLES entry:\n  "
        + "\n  ".join(sorted(double_booked))
    )


@pytest.mark.tripwire
def test_an_injected_uncovered_table_is_named_in_the_gap(tmp_path) -> None:  # noqa: ANN001
    """THE CONTROL (AC4): a migration adding a table with no registration and
    no excuse must make the tripwire fail, and name exactly that table --
    proves ``coverage_gap`` is a real diff, not a tautology that would pass
    no matter what the schema held."""
    from tests._schema_template import seed_schema

    path = tmp_path / "schema.db"
    seed_schema(path)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE an_ad_hoc_table_nobody_registered (id TEXT PRIMARY KEY)"
        )
        con.commit()
    finally:
        con.close()
    live_tables = _schema_tables(path)

    gap = coverage_gap(live_tables)

    assert gap == frozenset({"an_ad_hoc_table_nobody_registered"})
