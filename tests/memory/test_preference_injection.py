"""End-to-end test: preferences set via PreferenceStore appear in classify's memory_context."""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.preferences import GLOBAL_OWNER_KEY, PreferenceStore
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


async def test_global_preference_visible_to_any_session(tmp_db: DbPool) -> None:
    """A GLOBALLY-set output preference is surfaced to the model for a session
    that has no per-owner prefs — closing the awareness loop cross-channel."""
    bridge = SqliteMemoryBridge(db=tmp_db)
    store = PreferenceStore(db=tmp_db)
    await store.set(GLOBAL_OWNER_KEY, "output_tables", "off")

    token = set_services(StepServices(memory_bridge=bridge, preference_store=store))
    try:
        out = await classify.run(_make_state(session_id="sess-fresh"))
    finally:
        reset_services(token)

    ctx = out.memory_context or ""
    assert "## Learned Preferences" in ctx
    assert "output_tables: off" in ctx


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


async def test_preferences_pinned_to_top_of_memory_context(tmp_db: DbPool) -> None:
    """Preferences stay pinned to the top of memory_context.

    Plan A (RC-C) moved recent conversation OUT of memory_context into
    state.history (real message turns), so the old "prefs before Recent
    conversation:" ordering no longer applies. This verifies prefs remain
    pinned to the top of memory_context and the recent turn surfaces in history.
    """
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
    # Prefs remain pinned to the very top of memory_context.
    assert ctx.lstrip().startswith("## Learned Preferences")
    # Recent conversation no longer lives in memory_context (moved to history).
    assert "Recent conversation:" not in ctx
    # The prior turn now surfaces as a real history message instead.
    assert any("hi" in m.content for m in out.history)


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
