"""One clock, one format — and the exceptions are named rather than rediscovered.

WHY THIS EXISTS, and the evidence is that it caught me twice inside ten minutes.

This database stores time two ways. `jobs.run_at` is an ISO-8601 string;
`task_outcomes.captured_at` is a Unix epoch REAL. Nothing in the column NAME says
which — both are `*_at` — and SQLite will happily compare either against anything
without an error. So:

    SELECT substr(captured_at,1,10), COUNT(*) FROM task_outcomes GROUP BY 1

returns rows grouped by `9999`, `9998`, `9997` — the leading digits of an epoch — and
reads exactly like a table of counts per day. MEASURED 2026-09-06: I ran that query,
got a plausible-looking answer, and it was meaningless. The same session then hit
`no such column: created_at` on two tables that use `run_at` and `captured_at`. In a
programme whose entire method is measuring this database, a store that answers the
wrong question without erroring is the instrument lying.

**THE CONVENTION ALREADY SETTLED — IT WAS NEVER WRITTEN DOWN.** Measured across the
shipped migrations: 89 time-ish column declarations are TEXT and 28 are REAL, and the
split is chronological, not arbitrary. The last migration to declare an epoch time
column is **0107**. Every one of the nine time columns added since (0115 through 0128)
is ISO TEXT. So the practice is right and unenforced — the same shape as the "the full
suite hangs" line that survived in three method documents after being corrected, and
as the ruff/mypy baselines that read 39/78 in prose against a gate of 35/65.

This guard reads the MIGRATIONS rather than the live database, deliberately: the
migrations are what ships to every clone, and CI has no database at all. A guard that
needed the operator's box would be a guard that never runs.

**The legacy columns are NOT migrated.** Rewriting 28 columns of historical timestamps
is a destructive data migration on the state of record, which is the operator's call,
not an autonomous one. Naming them is what removes the hazard: a query author can see
which tables need epoch comparisons instead of discovering it by publishing a wrong
number.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "stackowl" / "db" / "migrations"

#: A column whose name ends this way stores a moment in time.
_TIMEISH = re.compile(r"(_at|_time|timestamp|_ts)$", re.I)

#: `name TYPE` in a CREATE TABLE body or after ADD COLUMN.
_DECL = re.compile(
    r"(?:ADD\s+COLUMN\s+|^\s*)([a-z_][a-z0-9_]*)\s+(TEXT|REAL|INTEGER|NUMERIC)\b",
    re.I | re.M,
)

#: The last migration that declared an epoch time column. Everything after it is ISO,
#: measured rather than asserted — see the module docstring.
_EPOCH_ERA_ENDS = 107


def _declarations() -> list[tuple[int, str, str, str]]:
    """(migration number, column, declared type, file) for every time-ish column.

    Comments are stripped first. This guard's own docstring names column types, and a
    rule that matches its own explanation is a rule that cannot fail — this programme
    has already shipped that defect three times.
    """
    out: list[tuple[int, str, str, str]] = []
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        body = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
        for name, typ in _DECL.findall(body):
            if _TIMEISH.search(name):
                out.append((int(path.name[:4]), name, typ.upper(), path.name))
    return out


def _epoch_declarations() -> list[tuple[int, str, str, str]]:
    return [d for d in _declarations() if d[2] in {"REAL", "INTEGER", "NUMERIC"}]


class TestNewTimeColumnsAreISO:
    @pytest.mark.tripwire
    def test_no_migration_after_the_epoch_era_declares_an_epoch_time_column(self) -> None:
        """THE RULE. New time columns are ISO-8601 TEXT in UTC.

        Not "prefer" — a second representation is what makes a `WHERE created_at >=
        '2026-09-01'` silently correct on one table and silently wrong on another.
        """
        strays = [
            f"{f}: {name} {typ}"
            for num, name, typ, f in _epoch_declarations()
            if num > _EPOCH_ERA_ENDS
        ]

        assert not strays, (
            "these migrations store a moment in time as a number, after the project "
            "settled on ISO-8601 TEXT at migration "
            f"{_EPOCH_ERA_ENDS:04d}: {strays}"
        )

    def test_the_convention_is_written_where_a_human_reads_it(self) -> None:
        """A rule enforced only by a test is a rule nobody knows about until it fires."""
        process = (
            Path(__file__).resolve().parents[2] / "CLAUDE.md"
        ).read_text(encoding="utf-8")

        assert "One clock, one format" in process, (
            "the timestamp convention is enforced by this test and written nowhere"
        )


class TestTheLegacyEpochColumnsAreNamed:
    """The hazard is not that they exist — it is that nothing says which they are."""

    @pytest.mark.tripwire
    def test_the_epoch_era_did_not_grow(self) -> None:
        """A count, not a list, so adding a table in the legacy range is caught without
        this test becoming a transcription of the schema."""
        assert len(_epoch_declarations()) == 28, (
            f"the set of epoch time columns changed: {len(_epoch_declarations())} "
            "declarations now. If a migration was edited, the history a reader relies "
            "on to know which columns need epoch comparisons has moved."
        )

    def test_the_parse_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. A regex that matched nothing would pass every assertion
        above, and the epoch-era test would fail loudly rather than silently — which is
        why the ISO side is asserted here too."""
        decls = _declarations()

        assert len(decls) >= 100, f"only parsed {len(decls)} time columns"
        assert sum(1 for d in decls if d[2] == "TEXT") >= 80
        assert len(list(_MIGRATIONS.glob("*.sql"))) >= 100

    def test_the_era_boundary_is_real_and_not_just_asserted(self) -> None:
        """The boundary is a measurement: ISO columns really were added after it."""
        after = [d for d in _declarations() if d[0] > _EPOCH_ERA_ENDS]

        assert after, "no time column has been added since the boundary — it is unproven"
        assert all(d[2] == "TEXT" for d in after), (
            f"the boundary is wrong; these came after it: {after}"
        )
