"""End-to-end test: preferences set via PreferenceStore appear in classify's memory_context."""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.preferences import PreferenceStore
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import classify

pytestmark = pytest.mark.asyncio


def _make_state(session_id: str = "sess-X") -> PipelineState:
    return PipelineState(
        trace_id=f"trace-{session_id}",
        session_id=session_id,
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="start",
    )


async def test_preferences_appear_in_classify_memory_context(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    store = PreferenceStore(db=tmp_db)
    await store.set("sess-X", "response_style", "markdown bullets")
    await store.set("sess-X", "language", "English")

    token = set_services(StepServices(
        memory_bridge=bridge,
        preference_store=store,
    ))
    try:
        out = await classify.run(_make_state(session_id="sess-X"))
    finally:
        reset_services(token)

    ctx = out.memory_context or ""
    assert "## Learned Preferences" in ctx
    assert "response_style: markdown bullets" in ctx
    assert "language: English" in ctx


async def test_no_preferences_section_when_owner_has_none(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    store = PreferenceStore(db=tmp_db)
    # Set a pref for a different session — must not leak.
    await store.set("other-session", "k", "v")

    token = set_services(StepServices(
        memory_bridge=bridge,
        preference_store=store,
    ))
    try:
        out = await classify.run(_make_state(session_id="sess-empty"))
    finally:
        reset_services(token)

    ctx = out.memory_context or ""
    assert "Learned Preferences" not in ctx


async def test_preferences_section_appears_before_recent_conversation(tmp_db: DbPool) -> None:
    """Preferences should be pinned to the top of memory_context."""
    bridge = SqliteMemoryBridge(db=tmp_db)
    store = PreferenceStore(db=tmp_db)
    await bridge.store("User: hi\n\nAssistant: hello", "sess-A")
    await store.set("sess-A", "response_style", "terse")

    token = set_services(StepServices(
        memory_bridge=bridge,
        preference_store=store,
    ))
    try:
        out = await classify.run(_make_state(session_id="sess-A"))
    finally:
        reset_services(token)

    ctx = out.memory_context or ""
    prefs_idx = ctx.find("## Learned Preferences")
    recent_idx = ctx.find("Recent conversation:")
    assert prefs_idx >= 0
    assert recent_idx >= 0
    assert prefs_idx < recent_idx


async def test_classify_does_not_crash_when_store_is_none(tmp_db: DbPool) -> None:
    bridge = SqliteMemoryBridge(db=tmp_db)
    token = set_services(StepServices(
        memory_bridge=bridge, preference_store=None,
    ))
    try:
        out = await classify.run(_make_state())
    finally:
        reset_services(token)
    # No preferences section — just no crash.
    ctx = out.memory_context or ""
    assert "Learned Preferences" not in ctx
