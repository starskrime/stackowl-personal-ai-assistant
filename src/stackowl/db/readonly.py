"""Open a SQLite database for READING without ever being able to create one.

WHY THIS EXISTS. `sqlite3.connect(path)` creates the file when it is missing — that is
how `~/.stackowl/stackowl.db` kept reappearing as a 0-byte decoy beside the config. A
`file:…?mode=ro` URI stops that for the main file, and MEASURED 2026-09-12 it is not
enough on its own: on a read-only connection `ATTACH '<new>.db'` and
`VACUUM INTO '<new>.db'` both still create a new database, because SQLite opens an
attachment with the connection's original read-write flags. Allowing zero attachments
closes both.

WHAT IT DOES NOT PROMISE. It never creates a DATABASE file; it can still create files
SQLite owns. MEASURED 2026-09-12: a read-only open of a WAL database with no `-wal` /
`-shm` beside it creates both, and they stay after close — the main file is unchanged.
TEMP tables also work: they live in SQLite's temp store, not in the file.

ONE PLACE, so a reader that must not write asks here instead of re-typing the URI —
the health ping, the scheduler-progress supervisor and `stackowl db query` all open
this way.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

__all__ = ["connect_read_only", "read_only_uri"]

log = logging.getLogger("stackowl.db")


def read_only_uri(path: Path) -> str:
    """The `mode=ro` URI for ``path``.

    `as_uri()` rather than an f-string over the path: a Windows path is `C:\\...` and a
    POSIX one may hold a space or a `?`, and both make a hand-built `file:` URI silently
    wrong.
    """
    return f"{Path(path).resolve().as_uri()}?mode=ro"


def connect_read_only(path: Path) -> sqlite3.Connection:
    """A connection that cannot write ``path`` and cannot create any other database.

    Raises ``sqlite3.OperationalError`` ("unable to open database file") when ``path``
    does not exist — no database file is created. SQLite may still create ``-wal`` /
    ``-shm`` beside an existing WAL database.
    """
    # 1. ENTRY
    log.debug("[db] connect_read_only: entry — path=%s", path)
    conn = sqlite3.connect(read_only_uri(path), uri=True)
    try:
        # 2. DECISION / 3. STEP — no attachments, so no statement can open a second
        #    file read-write (ATTACH and VACUUM INTO both attach under the hood).
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    except Exception:
        log.warning("[db] connect_read_only: could not refuse attachments — closing", exc_info=True)
        conn.close()
        raise
    # 4. EXIT
    log.debug("[db] connect_read_only: exit — read-only, attachments refused")
    return conn
