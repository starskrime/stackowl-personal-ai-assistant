"""Migration 0125 must actually MOVE work, not just apply cleanly.

Applying a migration to an empty database proves nothing about what it does —
the same zero-denominator trap that made "0 warnings" look like a pass earlier in
this programme. This box happens to have no pending retry_queue rows, so the only
way to know 0125 is correct is to give it rows and check where they land.

WHAT COULD GO WRONG, and therefore what is tested:

* retry_queue had NO uniqueness constraint, so a database predating the dedup fix
  can hold SEVERAL pending rows for one session. Migration 0124 enforces one live
  task per (owner, idempotency_key), so a naive INSERT would violate the index and
  ABORT THE WHOLE MIGRATION on exactly the devices that most need it.
* A destination of "telegram" with no address makes delivery impossible, so the
  task never completes and retries for ever (81f6b7ec). The address has to be
  rebuilt from channel_chat_id.
* Resetting attempt_count would hand an already-failing row a fresh budget, which
  is the unbounded behaviour this collapse exists to remove.
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile

from stackowl.db.migrations.runner import MigrationRunner

_SQL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src" / "stackowl" / "db" / "migrations"
    / "0125_retry_queue_rows_move_to_the_one_loop.sql"
).read_text(encoding="utf-8")


def _db_with_pending_rows() -> sqlite3.Connection:
    """A schema-complete DB holding retry rows, as an upgrading device would."""
    path = pathlib.Path(tempfile.mkdtemp()) / "t.db"
    MigrationRunner(path).run()
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM tasks")
    rows = [
        # TWO pending rows for ONE session — legal in retry_queue, and the case
        # that would abort the migration under 0124's unique index.
        ("r1", "o1", "sess-a", "the older ask", "telegram", "72055773", 3),
        ("r2", "o1", "sess-a", "the NEWER ask", "telegram", "72055773", 7),
        # A different session, and a channel with no address (CLI).
        ("r3", "o1", "sess-b", "cli work", "cli", None, 0),
    ]
    for rid, owner, sess, goal, chan, chat, attempts in rows:
        conn.execute(
            "INSERT INTO retry_queue (id, owner_id, trace_id, session_key, goal, "
            "channel, channel_chat_id, attempt_count, status, next_retry_at, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,'pending','2026-08-28T00:00:00',"
            "'2026-08-28T00:00:00','2026-08-28T00:00:00')",
            (rid, owner, rid, sess, goal, chan, chat, attempts),
        )
    conn.commit()
    return conn


def _apply_0125(conn: sqlite3.Connection) -> None:
    conn.executescript(_SQL)
    conn.commit()


def test_pending_retries_become_tasks() -> None:
    """THE point of the migration. Work must not be stranded when its engine goes."""
    conn = _db_with_pending_rows()

    _apply_0125(conn)

    n = conn.execute("SELECT COUNT(*) FROM tasks WHERE trigger_kind='retry'").fetchone()[0]
    assert n == 2, f"expected one task per session, got {n}"


def test_two_pending_rows_for_one_session_do_not_abort_the_migration() -> None:
    """The collision case, which is the one that would break a real upgrade.

    Keeping MAX(rowid) mirrors the live path's choice to repoint a queued retry at
    the newer ask (incident 2026-07-21).
    """
    conn = _db_with_pending_rows()

    _apply_0125(conn)

    goals = [r[0] for r in conn.execute(
        "SELECT goal FROM tasks WHERE session_key='sess-a'"
    )]
    assert goals == ["the NEWER ask"], f"kept the wrong ask: {goals}"


def test_the_migrated_task_knows_WHERE_to_reply() -> None:
    """A bare channel name is not an address — 81f6b7ec."""
    conn = _db_with_pending_rows()

    _apply_0125(conn)

    dest = conn.execute(
        "SELECT destination FROM tasks WHERE session_key='sess-a'"
    ).fetchone()[0]
    assert dest == "telegram:72055773", dest


def test_a_channel_with_no_address_survives_honestly() -> None:
    """CLI addresses its one terminal implicitly; inventing a recipient is worse."""
    conn = _db_with_pending_rows()

    _apply_0125(conn)

    dest = conn.execute(
        "SELECT destination FROM tasks WHERE session_key='sess-b'"
    ).fetchone()[0]
    assert dest == "cli", dest


def test_attempts_already_spent_are_carried_over() -> None:
    """Resetting would hand a failing row a fresh budget — the unbounded
    behaviour this collapse removes."""
    conn = _db_with_pending_rows()

    _apply_0125(conn)

    attempts = conn.execute(
        "SELECT attempt_count FROM tasks WHERE session_key='sess-a'"
    ).fetchone()[0]
    assert attempts == 7, attempts


def test_the_moved_rows_are_closed_and_the_sweep_is_retired() -> None:
    """Leaving them pending would let a still-running old sweep double-drive them."""
    conn = _db_with_pending_rows()
    conn.execute(
        "INSERT INTO jobs (job_id, handler_name, schedule, status, created_at, "
        "idempotency_key, next_run_at) "
        "VALUES ('retry_sweep-x','retry_sweep','every 1m','pending',"
        "'2026-08-28T00:00:00','retry_sweep-x','2026-08-28T00:00:00')"
    )
    conn.commit()

    _apply_0125(conn)

    still_pending = conn.execute(
        "SELECT COUNT(*) FROM retry_queue WHERE status='pending'"
    ).fetchone()[0]
    assert still_pending == 0
    sweeps = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE handler_name='retry_sweep'"
    ).fetchone()[0]
    assert sweeps == 0, "the every-minute sweep for a dead engine is still scheduled"


def test_the_boot_path_does_not_RE_SEED_the_sweep() -> None:
    """Deleting the job row is useless while something re-creates it.

    MEASURED 2026-08-29: migration 0125 ran at 00:31:02 and the retry_sweep job
    reappeared at 00:31:33 — 31 seconds later — because scheduler assembly seeded
    it again at boot. The migration had been reported as "retiring the sweep"; it
    retired one row, every boot, for thirty-one seconds.

    Removing a row without removing its WRITER just means the row comes back. This
    fails if the seed returns.
    """
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "stackowl" / "scheduler" / "assembly.py"
    ).read_text(encoding="utf-8")

    assert 'handler_name="retry_sweep"' not in src, (
        "scheduler assembly seeds retry_sweep again, so migration 0125's DELETE "
        "is undone on the next boot"
    )
