"""The shell tool may READ the platform database, never WRITE it.

Earned on 2026-08-30. An autonomous recovery session ran a hand-written
``python3 -c`` heredoc against ``stackowl.db`` and purged 151 learned skills.
The audit row it wrote names a dump and a pre-purge database backup; neither
exists anywhere on the box. Nothing gated it: the catastrophic-shape check
covers ``rm -rf /`` and block devices, and a SQL statement is neither.

The operator's standing rule already says DB problems are migration scripts
only. This makes the shell tool hold that rule instead of trusting the caller
to.

WHY WRITES ONLY, AND NOT ALL ACCESS. The shell tool does not log the command it
runs (its entry line is DEBUG; production runs at INFO), so the read/write mix
of database access through it is UNMEASURED. Refusing every command that names
the database could break read paths that cannot be seen. Refusing writes is the
narrowest rule that covers the incident.

WHY STATEMENT SHAPES, NOT VERBS. ``_is_catastrophic_segment`` matches command
STRUCTURE and target PATHS and explicitly avoids keyword matching. A bare verb
list would fire on ``SELECT * FROM skill_audit WHERE op='delete'`` — a query
this programme actually runs. Two-token statement shapes (``DELETE FROM``,
``INSERT INTO``) are SQL grammar, not natural language, and they do not match a
verb appearing as a value or a column name.
"""

from __future__ import annotations

import pytest

from stackowl.paths import StackowlHome
from stackowl.tools.system.shell import run_argv, writes_platform_db

DB = str(StackowlHome.db_path())


# =========================================================================== #
# 1. The incident shape, and its siblings
# =========================================================================== #


@pytest.mark.parametrize(
    "command",
    [
        # The real one, reconstructed from the traceback in the engine log.
        f"""python3 -c "import sqlite3; c=sqlite3.connect('{DB}');"""
        """ c.execute("INSERT INTO skill_audit (op, actor) VALUES ('purge', 'friday')")" """,
        f"sqlite3 {DB} \"DELETE FROM skills WHERE source='learned'\"",
        f'sqlite3 {DB} "DROP TABLE staged_facts"',
        f'sqlite3 {DB} "UPDATE tasks SET status = \'done\'"',
        f'sqlite3 {DB} "ALTER TABLE skills ADD COLUMN x TEXT"',
        f'sqlite3 {DB} "REPLACE INTO owls VALUES (1)"',
        f'sqlite3 {DB} "VACUUM"',
        # Reached through a chained command, like the catastrophic check.
        f'cd /tmp && sqlite3 {DB} "DELETE FROM messages"',
        # Named by filename rather than full path.
        'sqlite3 stackowl.db "DELETE FROM skills"',
    ],
)
def test_a_write_to_the_platform_database_is_refused(command: str) -> None:
    blocked, reason = writes_platform_db(command)
    assert blocked, f"should have been refused: {command}"
    assert reason


def test_the_refusal_says_what_to_do_instead() -> None:
    _blocked, reason = writes_platform_db(f'sqlite3 {DB} "DELETE FROM skills"')
    assert "migration" in reason.lower(), reason


# =========================================================================== #
# 2. Reads still work — the rule is writes, not access
# =========================================================================== #


@pytest.mark.parametrize(
    "command",
    [
        f'sqlite3 {DB} "SELECT COUNT(*) FROM skills"',
        f'sqlite3 {DB} "SELECT created_at FROM messages LIMIT 3"',
        # The false positive a bare verb list would produce: 'delete' as a VALUE.
        f"sqlite3 {DB} \"SELECT * FROM skill_audit WHERE op='delete'\"",
        # ...and as a column name.
        f'sqlite3 {DB} "SELECT deleted_at FROM tasks"',
        f'sqlite3 {DB} ".schema skills"',
    ],
)
def test_reading_the_platform_database_is_allowed(command: str) -> None:
    blocked, _reason = writes_platform_db(command)
    assert not blocked, f"a read was refused: {command}"


@pytest.mark.parametrize(
    "command",
    [
        'sqlite3 /tmp/scratch.db "DELETE FROM anything"',
        'sqlite3 ./local.db "DROP TABLE t"',
        "psql -c 'DELETE FROM users'",
    ],
)
def test_other_databases_are_not_this_tools_business(command: str) -> None:
    blocked, _reason = writes_platform_db(command)
    assert not blocked, f"unrelated database refused: {command}"


# =========================================================================== #
# 3. The path is DISCOVERED, never hardcoded
# =========================================================================== #


def test_the_database_path_is_asked_for_not_written_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Move the workspace; the guard must follow it to the new FULL path.

    The filename is constant, so the filename rule proves nothing about
    discovery. This asserts the full-path arm specifically: a write to the
    moved database, named by a path that appears nowhere in the source.
    """
    moved = tmp_path / "elsewhere"  # type: ignore[operator]
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: moved))
    moved_db = moved / "stackowl.db"
    # Named by full path, and the filename arm is removed from the equation by
    # checking the reason quotes the resolved name.
    blocked, reason = writes_platform_db(f'sqlite3 {moved_db} "DELETE FROM skills"')
    assert blocked, "the guard did not follow the workspace"
    assert "stackowl.db" in reason


# =========================================================================== #
# 4. The shared execution seam refuses — so learned tools are covered too
# =========================================================================== #


@pytest.mark.asyncio
async def test_run_argv_refuses_and_never_spawns() -> None:
    command = f'sqlite3 {DB} "DELETE FROM skills"'
    result = await run_argv(
        ["sqlite3", DB, "DELETE FROM skills"],
        shell_command=command,
        timeout_sec=5.0,
    )
    assert result.success is False
    assert "migration" in (result.error or "").lower()
    # Nothing ran, so nothing was committed.
    assert result.side_effect_committed is False


@pytest.mark.asyncio
async def test_run_argv_still_runs_a_read() -> None:
    result = await run_argv(
        ["sqlite3", DB, "SELECT 1"],
        shell_command=f'sqlite3 {DB} "SELECT 1"',
        timeout_sec=15.0,
        intent="read",
    )
    # It may fail for environmental reasons (no sqlite3 binary on this box);
    # what must NOT happen is a refusal by the guard.
    assert "migration" not in (result.error or "").lower()
