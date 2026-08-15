"""Every staged_facts row is bounded, whatever its source_type (I7).

D08.1 bounded the conversation buffer after removing the fact pipeline that used
to consume it. That bound is scoped `WHERE source_type = 'conversation' AND
source_ref = ?`, which is correct for conversation — one session must not evict
another's history — and useless for everything else.

MEASURED on the live database 2026-08-14, which is why it is useless:

    source_type            rows   distinct source_refs   max rows per ref
    agent_self            2,971            2,971                1
    conversation          2,741              599               60
    webpage                  10                9                2
    conversation_summary      6                5                2

`agent_self` carries the turn's trace_id as its source_ref, so EVERY ROW HAS A
UNIQUE REF. A per-ref trim keeping the newest N matches nothing, forever. Simply
widening the existing predicate would have been a write with no effect — the
exact shape this programme keeps finding — so the non-conversation types get a
per-TYPE cap instead.

The cap reuses `_TURN_HISTORY_FLOOR` rather than inventing a number. These rows
have no rich reader: every staged_facts SELECT in the bridge filters
`source_type = 'conversation'`, and the only way a non-conversation row surfaces
at all is `list_staged`, used for id-prefix lookups from the `memory` tool. So
the cap exists to keep a forensic tail, not to serve a query.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from stackowl.memory.models import StagedFact


def _fact(source_type: str, source_ref: str, n: int) -> StagedFact:
    """A staged fact with a distinct timestamp so 'newest' is well defined."""
    return StagedFact(
        content=f"{source_type}-{n}",
        source_type=source_type,
        source_ref=source_ref,
        confidence=0.5,
        staged_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        + datetime.timedelta(minutes=n),
        trust="self",
    )


async def _count(db: Any, source_type: str) -> int:
    rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM staged_facts WHERE source_type = ?", (source_type,)
    )
    return int(rows[0]["n"])


@pytest.mark.asyncio
async def test_a_unique_ref_type_is_still_bounded(tmp_db: Any) -> None:
    """The whole point: agent_self rows each have their own source_ref, so the
    per-ref trim can never match them. They must be capped per TYPE."""
    from stackowl.memory.sqlite_bridge import _TURN_HISTORY_FLOOR, SqliteMemoryBridge

    bridge = SqliteMemoryBridge(tmp_db)
    over = _TURN_HISTORY_FLOOR + 25
    for i in range(over):
        await bridge.stage(_fact("agent_self", f"trace-{i}", i))

    remaining = await _count(tmp_db, "agent_self")
    assert remaining <= _TURN_HISTORY_FLOOR, (
        f"agent_self must be capped per type; {remaining} rows survived {over} writes"
    )


@pytest.mark.asyncio
async def test_the_bound_keeps_the_NEWEST_rows(tmp_db: Any) -> None:
    """A forensic tail is only useful if it is the recent end of it."""
    from stackowl.memory.sqlite_bridge import _TURN_HISTORY_FLOOR, SqliteMemoryBridge

    bridge = SqliteMemoryBridge(tmp_db)
    for i in range(_TURN_HISTORY_FLOOR + 10):
        await bridge.stage(_fact("agent_self", f"trace-{i}", i))

    rows = await tmp_db.fetch_all(
        "SELECT content FROM staged_facts WHERE source_type = 'agent_self'"
    )
    kept = {r["content"] for r in rows}
    assert "agent_self-0" not in kept, "the oldest row must be trimmed"
    newest = f"agent_self-{_TURN_HISTORY_FLOOR + 9}"
    assert newest in kept, f"the newest row must survive; kept={sorted(kept)[:3]}…"


@pytest.mark.asyncio
async def test_one_type_does_not_evict_another(tmp_db: Any) -> None:
    """Types are capped independently — a burst of agent_self rows must not cost
    the conversation buffer, which is the only half anything actually reads."""
    from stackowl.memory.sqlite_bridge import _TURN_HISTORY_FLOOR, SqliteMemoryBridge

    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.stage(_fact("conversation_summary", "sess-a", 1))
    for i in range(_TURN_HISTORY_FLOOR + 20):
        await bridge.stage(_fact("agent_self", f"trace-{i}", 100 + i))

    assert await _count(tmp_db, "conversation_summary") == 1, (
        "trimming one source_type must not touch another"
    )


@pytest.mark.asyncio
async def test_conversation_is_still_bounded_per_session_not_per_type(
    tmp_db: Any,
) -> None:
    """UNCHANGED behaviour, asserted so the widening cannot regress it: two busy
    sessions must each keep their own history rather than competing for one
    global cap."""
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

    bridge = SqliteMemoryBridge(tmp_db)
    for i in range(30):
        await bridge.store(f"a-{i}", "sess-a")
        await bridge.store(f"b-{i}", "sess-b")

    for sess in ("sess-a", "sess-b"):
        rows = await tmp_db.fetch_all(
            "SELECT COUNT(*) AS n FROM staged_facts "
            "WHERE source_type = 'conversation' AND source_ref = ?",
            (sess,),
        )
        assert int(rows[0]["n"]) == 30, (
            f"{sess} lost turns — conversation must stay bounded PER SESSION"
        )


def test_the_migration_cap_matches_the_code_constant() -> None:
    """The one number, in the two places it has to live.

    Migration 0114 applies this bound to the existing backlog and cannot import
    a Python constant, so the cap appears both in SQL and in
    `_TURN_HISTORY_FLOOR`. Two copies of one rule is the shape this codebase
    keeps having to fix — this test is the "have the other ask it" that a SQL
    file cannot do for itself. If either side is changed alone, this fails.
    """
    import re
    from pathlib import Path

    from stackowl.memory.sqlite_bridge import _TURN_HISTORY_FLOOR

    sql = (
        Path(__file__).resolve().parents[2]
        / "src" / "stackowl" / "db" / "migrations"
        / "0114_bound_staged_facts_by_source_type.sql"
    ).read_text(encoding="utf-8")

    caps = re.findall(r"WHERE rn <= (\d+)", sql)
    assert caps == [str(_TURN_HISTORY_FLOOR)], (
        f"migration 0114 caps at {caps}, the code caps at {_TURN_HISTORY_FLOOR} — "
        "they must agree"
    )
