"""The conversation buffer must not grow forever (D08.1).

`staged_facts` is no longer a fact-staging queue. It is the short-term
conversation buffer — `classify` reads it every turn via
`recent_conversation_turns` — and it is the one thing that survived the
extraction pipeline's removal.

WHAT USED TO BOUND IT, and no longer does: mining consumed rows, and promotion
moved them into `committed_facts` where MemoryBudgetEnforcer pruned by byte
ceiling. Both are gone, and that enforcer sums a table that is now permanently
empty, so it can never fire again. Without a bound here every turn appends a row
forever — the same no-decay disease that grew the fact store to 107,576 rows,
one layer down, and created by the very change that removed it.
"""

from __future__ import annotations

import pytest

from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import (
    _TURN_HISTORY_FLOOR,
    SqliteMemoryBridge,
    _turns_to_keep,
)

pytestmark = pytest.mark.asyncio

_SCOPE = "owl:secretary:telegram:dm:1"


async def _write_turns(bridge: SqliteMemoryBridge, n: int, scope: str = _SCOPE) -> None:
    for i in range(n):
        await bridge.store(f"turn number {i}", scope)


async def _count(db, scope: str = _SCOPE) -> int:
    rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM staged_facts "
        "WHERE source_type = 'conversation' AND source_ref = ?",
        (scope,),
    )
    return int(str(rows[0]["n"]))


async def test_the_buffer_stops_growing_at_the_limit(tmp_db):
    """THE test. Write well past the retention window and confirm it plateaus."""
    bridge = SqliteMemoryBridge(tmp_db)
    keep = _turns_to_keep()

    await _write_turns(bridge, keep + 25)

    assert await _count(tmp_db) == keep


async def test_the_newest_turns_are_the_ones_kept(tmp_db):
    """Trimming the wrong end would silently delete the conversation the model
    is about to read — worse than not trimming at all."""
    bridge = SqliteMemoryBridge(tmp_db)
    keep = _turns_to_keep()

    await _write_turns(bridge, keep + 5)

    turns = await bridge.recent_conversation_turns(session_key=_SCOPE, limit=keep)
    contents = [t.content for t in turns]
    assert "turn number 0" not in contents, "oldest should have gone"
    assert f"turn number {keep + 4}" in contents, "newest must survive"


async def test_a_short_conversation_is_untouched(tmp_db):
    bridge = SqliteMemoryBridge(tmp_db)

    await _write_turns(bridge, 5)

    assert await _count(tmp_db) == 5


async def test_scopes_are_trimmed_independently(tmp_db):
    """One busy lane must not evict a quiet lane's history."""
    bridge = SqliteMemoryBridge(tmp_db)
    keep = _turns_to_keep()

    await _write_turns(bridge, keep + 10, scope="busy")
    await _write_turns(bridge, 3, scope="quiet")

    assert await _count(tmp_db, "busy") == keep
    assert await _count(tmp_db, "quiet") == 3


async def test_the_limit_follows_the_setting_that_governs_reads(monkeypatch):
    """Derived from short_term_window, not a magic number: raising the setting
    must not silently starve the only reader."""
    from stackowl.config import settings as settings_mod

    class _Mem:
        short_term_window = 40

    class _S:
        memory = _Mem()

    monkeypatch.setattr(settings_mod, "Settings", lambda *a, **k: _S())

    assert _turns_to_keep() == 400


async def test_a_small_window_still_gets_the_floor(monkeypatch):
    from stackowl.config import settings as settings_mod

    class _Mem:
        short_term_window = 2

    class _S:
        memory = _Mem()

    monkeypatch.setattr(settings_mod, "Settings", lambda *a, **k: _S())

    assert _turns_to_keep() == _TURN_HISTORY_FLOOR


async def test_unreadable_settings_fall_back_to_the_floor(monkeypatch):
    """A config problem must not make the buffer unbounded — an unbounded
    default here is the defect, not the safe case."""
    from stackowl.config import settings as settings_mod

    def _boom(*a, **k):
        raise RuntimeError("config on fire")

    monkeypatch.setattr(settings_mod, "Settings", _boom)

    assert _turns_to_keep() == _TURN_HISTORY_FLOOR


async def test_a_failing_trim_never_costs_the_turn_its_write(tmp_db, monkeypatch):
    """Losing the trim costs disk; losing the turn costs the user's short-term
    memory. The write must survive a broken trim."""
    bridge = SqliteMemoryBridge(tmp_db)

    async def _boom(*a, **k):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(tmp_db, "execute_returning_rowcount", _boom)

    await bridge.store("an important turn", _SCOPE)

    assert await _count(tmp_db) == 1


async def test_non_conversation_rows_are_not_trimmed(tmp_db):
    """The trim is scoped to conversation turns. Anything else in the table is
    not this mechanism's business."""
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.stage(StagedFact(
        content="not a conversation turn", source_type="agent_self",
        source_ref=_SCOPE, confidence=0.9,
    ))

    await _write_turns(bridge, _turns_to_keep() + 5)

    rows = await tmp_db.fetch_all(
        "SELECT COUNT(*) AS n FROM staged_facts WHERE source_type = 'agent_self'",
    )
    assert int(str(rows[0]["n"])) == 1
