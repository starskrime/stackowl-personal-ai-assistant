"""The all-clear counted rows it had just skipped.

MEASURED 2026-09-08 on the live database, from the platform's own log:

    [db] runner.verify: applied migration 0137 (0137_migration_semantic_checksum.sql)
         has no file in the tree — this database ran something no longer present
    [db] runner.verify: exit — 139 applied migrations match their files

Both lines, same boot, 192 times over the retained window. `schema_migrations`
holds **139** rows and **138** have a file; the one that does not is the one the
warning names. So the summary asserts that 139 migrations match files when 138
were compared and one could not be — and the summary is the line a reader trusts,
because it is the ALL CLEAR.

THE CAUSE IS THE DENOMINATOR, not the missing file. `verify` walks the applied
rows; a row with no file logs its warning and `continue`s — skipped from the
COMPARISON but not from `len(rows)`, which is the population the walk STARTED
with. The count was taken before the exclusions and reported after them.

AND THE ALL-CLEAR IS REACHED ON AN EMPTY COMPARISON. The `else` fires whenever
`drifted` is empty, which is also true when NOTHING was verified — a database
whose migration directory had gone missing entirely would log "0 applied
migrations match their files" as a pass. A zero numerator over a zero denominator
is not a pass either, and that is this repo's own rule about what a denominator is
MADE OF, found inside the integrity checker.

WHAT THIS IS NOT. The phantom row itself is a development artefact of this box —
`0137_migration_semantic_checksum.sql` was applied from the working tree on
2026-09-05 and never committed (`git log --all --diff-filter=ADR` finds it in no
commit). Deleting the row would silence the symptom on one machine and leave every
customer's integrity summary just as able to lie. The COUNT is the platform defect;
the row is the evidence that found it.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner, semantic_checksum


def _tree(tmp_path: Path, migrations: dict[str, str]) -> Path:
    """A migrations directory holding exactly `migrations` (version -> sql)."""
    d = tmp_path / "migrations"
    d.mkdir()
    for version, sql in migrations.items():
        (d / f"{version}_thing.sql").write_text(sql, encoding="utf-8")
    return d


def _applied(db: Path, rows: list[tuple[str, str, str | None]]) -> None:
    """Seed `schema_migrations` directly — the ledger, not the schema."""
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version TEXT PRIMARY KEY, name TEXT, applied_at TEXT,"
        "  checksum TEXT, sql_checksum TEXT)"
    )
    conn.executemany(
        "INSERT OR REPLACE INTO schema_migrations "
        "(version, name, applied_at, checksum, sql_checksum) VALUES (?,?,'now','x',?)",
        [(v, n, c) for v, n, c in rows],
    )
    conn.commit()
    conn.close()


def _verify(db: Path, mig_dir: Path, caplog: pytest.LogCaptureFixture) -> list[str]:
    """Run the real verify pass and return its messages."""
    caplog.set_level(logging.DEBUG)
    runner = MigrationRunner(db, mig_dir)
    files = [
        (f.name.split("_")[0], f.name, f)
        for f in sorted(Path(mig_dir).glob("*.sql"))
    ]
    runner._verify_applied(files)  # noqa: SLF001 — the pass under test
    return [r.getMessage() for r in caplog.records if "runner.verify" in r.getMessage()]


def test_the_summary_does_not_count_a_row_it_could_not_check(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """THE LIVE CASE, in miniature: three applied, one with no file."""
    sql = "CREATE TABLE t (a);\n"
    mig = _tree(tmp_path, {"0001": sql, "0002": sql})
    db = tmp_path / "x.db"
    _applied(db, [
        ("0001", "0001_thing.sql", semantic_checksum(sql)),
        ("0002", "0002_thing.sql", semantic_checksum(sql)),
        ("0003", "0003_gone.sql", "whatever"),      # applied, file deleted
    ])

    messages = _verify(db, mig, caplog)
    summary = [m for m in messages if "exit" in m]
    assert summary, "the pass stopped reporting a summary at all"
    assert " 3 " not in summary[0], (
        f"the all-clear counts the row it skipped: {summary[0]!r}. 3 applied, 2 "
        f"compared, and the summary is the line a reader trusts"
    )
    assert " 2 " in summary[0], f"the summary does not name what it verified: {summary[0]!r}"


def test_the_summary_says_how_many_it_could_NOT_check(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Naming the verified count alone is not enough.

    "2 applied migrations match their files" beside a ledger of 3 is arithmetic
    the reader has to do. The gap is the whole point of the line, so the line has
    to carry it — the per-row warning above it scrolls away, the summary does not.
    """
    sql = "CREATE TABLE t (a);\n"
    mig = _tree(tmp_path, {"0001": sql})
    db = tmp_path / "x.db"
    _applied(db, [
        ("0001", "0001_thing.sql", semantic_checksum(sql)),
        ("0003", "0003_gone.sql", "whatever"),
    ])

    summary = [m for m in _verify(db, mig, caplog) if "exit" in m]
    assert summary and ("1 " in summary[0] and "unverifiable" in summary[0].lower()), (
        f"the summary does not name the rows it could not check: {summary[0]!r}"
    )


def test_a_clean_ledger_still_reports_a_plain_all_clear(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """THE CONTROL. A fix that made every summary alarming would be worse than the
    defect — the point is that the all-clear becomes TRUE, not that it disappears."""
    sql = "CREATE TABLE t (a);\n"
    mig = _tree(tmp_path, {"0001": sql, "0002": sql})
    db = tmp_path / "x.db"
    _applied(db, [
        ("0001", "0001_thing.sql", semantic_checksum(sql)),
        ("0002", "0002_thing.sql", semantic_checksum(sql)),
    ])

    messages = _verify(db, mig, caplog)
    summary = [m for m in messages if "exit" in m]
    assert summary and "2" in summary[0]
    assert "unverifiable" not in summary[0].lower(), (
        f"a sound ledger is being reported as if something were wrong: {summary[0]!r}"
    )


def test_verifying_NOTHING_is_not_an_all_clear(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """0 OVER 0 IS NOT A PASS.

    The all-clear is reached whenever no drift was found, and "no drift" is also
    what an empty comparison produces. A database whose migration directory had
    gone missing entirely would have logged a pass over zero comparisons.
    """
    mig = _tree(tmp_path, {})
    db = tmp_path / "x.db"
    _applied(db, [("0001", "0001_thing.sql", "x"), ("0002", "0002_thing.sql", "x")])

    caplog.set_level(logging.DEBUG)
    _verify(db, mig, caplog)
    loud = [
        r for r in caplog.records
        if "runner.verify" in r.getMessage() and "exit" in r.getMessage()
    ]
    assert loud, "the pass said nothing at all about a ledger it could not check"
    assert loud[0].levelno >= logging.WARNING, (
        "two applied migrations, ZERO comparisons made, and the pass reported it at "
        "INFO as though the database had been checked and found sound"
    )
    # THE LEVEL ALONE WAS NOT ENOUGH, and mutation proved it: with this branch
    # deleted the `unverifiable` branch fires, which is ALSO a warning, so the
    # assertion above passed over code that had lost the property it names. Assert
    # the CLAIM, not the volume.
    assert "NOTHING VERIFIED" in loud[0].getMessage(), (
        f"the pass warned, but not that it had verified nothing: "
        f"{loud[0].getMessage()!r} — a reader cannot tell an unchecked ledger from a "
        f"partly-checked one"
    )
