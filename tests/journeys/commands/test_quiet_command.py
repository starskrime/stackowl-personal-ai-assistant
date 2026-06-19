"""Dispatch tests — /quiet scope matches its wording.

The original code said "session-scoped" / "for the current session" but the
notification_overrides table has no session_id column — the override is
global (process-wide).  The fix corrects the description/docstring to say
"global".  Tests assert:
  1. The description no longer claims session-scoping.
  2. Dispatching /quiet stores a row in the DB (global behaviour confirmed).
  3. Two sessions receive the same override (global, not per-session).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.quiet_command import QuietHoursCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


@pytest.fixture()
async def db(tmp_path: Path) -> DbPool:
    db_path = tmp_path / "quiet_test.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# Wording contract tests (no DB needed)
# ---------------------------------------------------------------------------


def test_quiet_description_does_not_claim_session_scope() -> None:
    """The description must not claim 'session' scoping — the table is global."""
    cmd = QuietHoursCommand()
    desc = cmd.description.lower()
    assert "session" not in desc, (
        f"Description still claims session scope: {cmd.description!r}"
    )


def test_quiet_description_indicates_global_scope() -> None:
    """The description must indicate global / process-wide scope."""
    cmd = QuietHoursCommand()
    desc = cmd.description.lower()
    assert "global" in desc or "process" in desc or "process-wide" in desc, (
        f"Description does not convey global scope: {cmd.description!r}"
    )


# ---------------------------------------------------------------------------
# Behaviour tests — confirms actual global storage
# ---------------------------------------------------------------------------


async def test_quiet_inserts_override_row(db: DbPool) -> None:
    """Dispatching /quiet stores one row in notification_overrides."""
    deps = CommandDeps(db=db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "quiet", "22:00 08:00", make_state()
    )

    assert "22:00-08:00" in result or "22:00" in result

    rows = await db.fetch_all("SELECT * FROM notification_overrides")
    assert len(rows) == 1
    assert rows[0]["start_time"] == "22:00"
    assert rows[0]["end_time"] == "08:00"
    assert rows[0]["category"] is None


async def test_quiet_override_is_global_not_per_session(db: DbPool) -> None:
    """The row written by one session is visible without any session filter.

    This confirms the table is global: there is no session_id column — the
    override applies process-wide regardless of which session issued it.
    We verify this by checking the stored row has no session_id field.
    """
    deps = CommandDeps(db=db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    state_a = make_state()
    state_b = make_state().model_copy(update={"session_id": "session-B"})

    await CommandRegistry.instance().dispatch("quiet", "23:00 07:00", state_a)
    # Dispatch from a second session — both land in the same global table
    CommandRegistry.reset()
    register_all_commands(deps, registry=CommandRegistry.instance())
    await CommandRegistry.instance().dispatch("quiet", "01:00 05:00", state_b)

    rows = await db.fetch_all("SELECT * FROM notification_overrides")
    # Two rows (one per call), neither has a session_id column
    assert len(rows) == 2, "Both overrides global — one row per call, no session isolation"
    col_names = set(rows[0].keys())
    assert "session_id" not in col_names, (
        "notification_overrides must not have session_id — override is global by schema"
    )


async def test_quiet_category_override(db: DbPool) -> None:
    """Per-category override stores the category name."""
    deps = CommandDeps(db=db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "quiet", "--category alerts 20:00 06:00", make_state()
    )

    assert "alerts" in result or "category:alerts" in result

    rows = await db.fetch_all("SELECT * FROM notification_overrides")
    assert len(rows) == 1
    assert rows[0]["category"] == "alerts"


async def test_quiet_invalid_time_format(db: DbPool) -> None:
    """Invalid time format returns honest error — no row inserted."""
    deps = CommandDeps(db=db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "quiet", "not-a-time 08:00", make_state()
    )

    assert "invalid" in result.lower() or "✗" in result
    rows = await db.fetch_all("SELECT * FROM notification_overrides")
    assert len(rows) == 0
