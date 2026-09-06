"""A version the live ledger already claims can never be a file again.

WHY THIS EXISTS, and it is a defect about EVERY installation rather than this box.

`MigrationRunner._pending` selects "versions present on disk and absent from
`schema_migrations`". So a version already recorded as applied is SKIPPED — its file
is never executed, and nothing says so.

MEASURED 2026-09-06 on the live database: `schema_migrations` holds **137 rows, the
last of them `0137`**, while the newest file on disk is `0136`. No `0137` file has
ever existed in git. The runner's own comment says why (`runner.py:42-45`): a
migration 0137 was written to add `schema_migrations.sql_checksum`, the author
judged the migrations ledger to be the wrong home for that and moved the logic into
the runner's bootstrap — after the row had been applied here.

**That makes 0137 permanently unusable as a filename, and unusably in the worst
way.** A future `0137_*.sql` would be SKIPPED on any database that has this row and
APPLIED on a fresh clone from the repository, so the operator's schema and every
downloaded copy would silently diverge — with no error on either side. That is the
"fix the platform, not this setup" case exactly: the trap is shipped in the numbering
scheme, not in one machine's data.

Deleting the row is not this guard's business — it is a write to the live migration
ledger, which is the operator's call (ESC-142). Reserving the number costs nothing
and closes the divergence whatever he decides.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATIONS = (
    Path(__file__).resolve().parents[2]
    / "src" / "stackowl" / "db" / "migrations"
)
_VERSION_RE = re.compile(r"^(\d{4})_")

#: Versions recorded in a live `schema_migrations` that never had a file, with the
#: reason each is burned. An entry is a promise that no file will ever take the
#: number, so each one states how the ledger came to claim it.
_RESERVED: dict[str, str] = {
    "0137": (
        "Applied on the operator's database as a migration adding "
        "schema_migrations.sql_checksum, then judged the wrong home and moved into "
        "MigrationRunner's bootstrap (runner.py:42-45); the file never reached git "
        "but the row remains. A file with this number would be SKIPPED there and "
        "APPLIED on a fresh clone, diverging the two schemas silently. ESC-142 "
        "holds the separate question of whether to delete the row."
    ),
}


def _versions_on_disk() -> list[str]:
    out = []
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        m = _VERSION_RE.match(path.name)
        if m:
            out.append(m.group(1))
    return out


class TestAReservedVersionNeverBecomesAFile:
    @pytest.mark.tripwire
    def test_no_migration_file_uses_a_reserved_number(self) -> None:
        on_disk = set(_versions_on_disk())
        clash = sorted(on_disk & set(_RESERVED))

        assert not clash, (
            "these migration files use a version some live ledger already records "
            "as applied, so they will be SKIPPED on an existing install and RUN on "
            f"a fresh clone: {clash}"
        )

    def test_every_reservation_states_how_the_ledger_claimed_it(self) -> None:
        """A reserved number with no reason is a number nobody can ever release."""
        for version, reason in _RESERVED.items():
            assert len(reason) > 120, f"{version} is reserved without a reason"
            assert "ESC-" in reason or "runner.py" in reason, (
                f"{version}'s reservation cites no evidence"
            )

    def test_the_guard_sees_a_real_population(self) -> None:
        """VACUITY CONTROL — if the glob found nothing, the clash set is empty for
        the wrong reason."""
        on_disk = _versions_on_disk()

        assert len(on_disk) >= 100, f"only found {len(on_disk)} migrations"
        assert len(set(on_disk)) == len(on_disk), "two migrations share a version"


class TestTheNumberingHasNoSILENTHoles:
    def test_every_gap_below_the_high_water_mark_is_declared(self) -> None:
        """A missing number is either reserved-with-a-reason or a mistake. Left
        undeclared it reads as "nothing was ever here", which is what made 0137
        available to the next author in the first place."""
        on_disk = _versions_on_disk()
        span = range(1, int(max(on_disk)) + 1)
        missing = {f"{n:04d}" for n in span} - set(on_disk)
        undeclared = sorted(missing - set(_RESERVED))

        assert not undeclared, (
            "these version numbers have no file and no reservation — a future "
            f"author cannot tell a free number from a burned one: {undeclared}"
        )
