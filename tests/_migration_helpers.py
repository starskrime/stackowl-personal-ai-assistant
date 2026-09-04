"""Build a schema as it stood AT a migration, not as it stands today.

WHY THIS EXISTS. A migration test that replays the WHOLE chain cannot test a
migration whose effects a later migration removes — it asserts against a table
the chain itself drops. Measured 2026-09-04 on the first full suite run to
complete in days: 8 of 10 failures were exactly this, all on ``retry_queue``::

    tests/db/test_migration_0082.py                          2
    tests/db/test_migration_0125_moves_retries_to_the_one_loop.py   6

    sqlite3.OperationalError: no such table: retry_queue

``0135_drop_the_retry_queue_table.sql`` drops it. Both files call
``MigrationRunner(path).run()`` — every migration, ending after 0135 — and then
INSERT into a table that no longer exists.

THE COVERAGE IS STILL WORTH HAVING, which is why this is a helper and not a
delete. 0125 still executes on any database that predates it, and its docstring
records what it protects: a device with several pending rows for one session
would abort the whole migration under 0124's unique index. Deleting the test
would drop that guard for exactly the upgrading devices that need it.

``MigrationRunner`` takes ``migrations_dir``, so a directory holding only the
files up to a version reproduces the schema as it stood then. That seam already
existed; nothing new is invented here.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

_MIGRATIONS = (
    Path(__file__).resolve().parents[1]
    / "src" / "stackowl" / "db" / "migrations"
)
_NUMBER = re.compile(r"^(\d{4})_")


def migrations_up_to(version: int) -> Path:
    """A temp directory holding every migration numbered <= ``version``.

    Returns the directory; the caller passes it as ``migrations_dir`` so the
    runner stops where the test needs it to.
    """
    out = Path(tempfile.mkdtemp()) / "migrations"
    out.mkdir(parents=True)
    for sql in sorted(_MIGRATIONS.glob("*.sql")):
        m = _NUMBER.match(sql.name)
        if m and int(m.group(1)) <= version:
            shutil.copy2(sql, out / sql.name)
    return out


def schema_at(version: int) -> Path:
    """A migrated database whose schema is as it stood AFTER ``version``."""
    from stackowl.db.migrations.runner import MigrationRunner

    path = Path(tempfile.mkdtemp()) / "t.db"
    MigrationRunner(path, migrations_dir=migrations_up_to(version)).run()
    return path
