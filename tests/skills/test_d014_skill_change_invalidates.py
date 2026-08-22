"""D01.4 — a skills-catalogue change clears every frozen prompt.

The skills catalogue is ~4153 chars of the frozen prompt — the largest movable
part of it — so enabling or disabling a skill genuinely invalidates every prompt
that contains it. Unlike an owl edit this is NOT owl-scoped: the catalogue is a
machine-wide fact, so `invalidate_all` is correct and its global reach across
principals matches migration 0102's "NO owner_id, DELIBERATELY".

BOUND TO THE WRITE, NOT THE CALLER. The invalidation lives next to the UPDATE in
the store rather than in `/skill enable`, so a future writer that reaches the
catalogue by another route still invalidates. That is the same reasoning that put
cache marking at one chokepoint in D01.2 — and the exact gap D01.2's cleanup
stage found when the stream path turned out to be marked but never instrumented.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.sessions.prompt_store import SessionPromptStore
from stackowl.skills.store import SkillIndexStore

pytestmark = pytest.mark.asyncio

LANE = "owl:secretary:telegram:dm:72055773"
RUN = "20260728_040000_d014bbbb"


async def _freeze(db: DbPool, owl: str) -> None:
    await SessionPromptStore(db).save(
        session_key=LANE, owl_name=owl, conversation_id=RUN,
        prompt_text=f"frozen prompt for {owl}", model_window=None,
    )


async def _a_skill(db: DbPool) -> int:
    """Insert one skills row directly and return its id."""
    await db.execute(
        "INSERT INTO skills (name, source, path, description, body_text, enabled, "
        "owner_id, loaded_at, updated_at) "
        "VALUES (?, 'local', ?, ?, ?, 1, 'principal-default', 0, 0)",
        ("pdf", "/tmp/pdf", "summarize pdfs", "the body"),
    )
    rows = await db.fetch_all("SELECT skill_id FROM skills WHERE name = ?", ("pdf",))
    return int(rows[0]["skill_id"])


async def test_toggling_a_skill_clears_every_frozen_prompt(tmp_db: DbPool) -> None:
    """Every owl's prompt carries the catalogue, so every owl's prompt is stale."""
    await _freeze(tmp_db, "secretary")
    await _freeze(tmp_db, "researcher")
    skill_id = await _a_skill(tmp_db)

    await SkillIndexStore(tmp_db).set_enabled(skill_id, enabled=False)

    store = SessionPromptStore(tmp_db)
    assert await store.load(session_key=LANE, owl_name="secretary", conversation_id=RUN) is None
    assert await store.load(session_key=LANE, owl_name="researcher", conversation_id=RUN) is None, (
        "a catalogue change must clear EVERY owl's prompt, not just one"
    )


async def test_the_skill_toggle_itself_still_works(tmp_db: DbPool) -> None:
    """Invalidation must not become a condition of the write succeeding."""
    skill_id = await _a_skill(tmp_db)
    await SkillIndexStore(tmp_db).set_enabled(skill_id, enabled=False)

    rows = await tmp_db.fetch_all(
        "SELECT enabled FROM skills WHERE skill_id = ?", (skill_id,)
    )
    assert int(rows[0]["enabled"]) == 0
