import pytest

from stackowl.infra import recovery_context as rc
from stackowl.pipeline.recovery_summary import surface_recovery
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(*, responses, language="en"):
    return PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="o", pipeline_step="deliver", responses=responses,
        language=language,
    )


def _answer(text="here is your answer", is_floor=False):
    return ResponseChunk(content=text, is_final=False, chunk_index=0,
                         trace_id="t", owl_name="o", is_floor=is_floor)


@pytest.mark.asyncio
async def test_appends_line_for_user_visible_recovery_on_real_answer():
    token = rc.bind()
    try:
        rc.record_recovery(kind="substitution", failed="browse_url",
                           recovered_via="http_fetch", user_visible=True)
        out = await surface_recovery(_state(responses=(_answer(),)))
        assert len(out.responses) == 2
        assert "browse_url" in out.responses[-1].content
        assert "http_fetch" in out.responses[-1].content
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_log_only_event_is_not_surfaced():
    token = rc.bind()
    try:
        rc.record_recovery(kind="provider_fallback", failed="big",
                           recovered_via="small", user_visible=False)
        s = _state(responses=(_answer(),))
        out = await surface_recovery(s)
        assert out.responses == s.responses
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_no_recovery_means_unchanged():
    token = rc.bind()
    try:
        s = _state(responses=(_answer(),))
        out = await surface_recovery(s)
        assert out.responses == s.responses
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_floor_only_response_surfaces_attempted_recovery_line():
    """F-10: when floored, the user-visible recovery is NOT silently dropped — a
    brief, generic 'I tried alternatives before giving up' line is surfaced so the
    honest floor still admits an attempt was made (no provider/tool names leaked)."""
    token = rc.bind()
    try:
        rc.record_recovery(kind="substitution", failed="secret_tool",
                           recovered_via="secret_sibling", user_visible=True)
        s = _state(responses=(_answer("I couldn't finish", is_floor=True),))
        out = await surface_recovery(s)
        # Original floor preserved + ONE brief attempted-recovery line appended.
        assert len(out.responses) == 2
        line = out.responses[-1].content
        assert line.strip()
        # Generic — must not leak the specific failed/recovered capability names.
        assert "secret_tool" not in line
        assert "secret_sibling" not in line
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_floor_with_no_recovery_events_stays_unchanged():
    """No recovery events at all → a floored response is left byte-identical."""
    token = rc.bind()
    try:
        s = _state(responses=(_answer("I couldn't finish", is_floor=True),))
        out = await surface_recovery(s)
        assert out.responses == s.responses
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_recovery_line_localized_to_turn_language():
    """F-9: the recovery trace honors state.language (not a hardcoded 'en')."""
    token = rc.bind()
    try:
        rc.record_recovery(kind="substitution", failed="browse_url",
                           recovered_via="http_fetch", user_visible=True)
        out = await surface_recovery(
            _state(responses=(_answer(),), language="de")
        )
        assert len(out.responses) == 2
        # German template: "'{failed}' war nicht verfügbar, daher habe ich ..."
        assert "war nicht verfügbar" in out.responses[-1].content
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_cap_at_two_recovery_events():
    token = rc.bind()
    try:
        for i in range(3):
            rc.record_recovery(kind="substitution", failed=f"tool{i}",
                               recovered_via=f"sib{i}", user_visible=True)
        out = await surface_recovery(_state(responses=(_answer(),)))
        # 1 real answer + capped 2 annotation lines = 3 chunks
        assert len(out.responses) == 3
        assert "tool0" in out.responses[1].content and "sib0" in out.responses[1].content
        assert "tool1" in out.responses[2].content and "sib1" in out.responses[2].content
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_provider_fallback_renders_generic_line_without_names():
    token = rc.bind()
    try:
        rc.record_recovery(kind="provider_fallback", failed="gpt-secret-name",
                           recovered_via="other-secret-name", user_visible=True)
        out = await surface_recovery(_state(responses=(_answer(),)))
        assert len(out.responses) == 2
        line = out.responses[-1].content
        assert "backup" in line.lower()
        assert "gpt-secret-name" not in line
        assert "other-secret-name" not in line
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_unknown_kind_is_not_surfaced():
    token = rc.bind()
    try:
        rc.record_recovery(kind="some_future_kind", failed="a",
                           recovered_via="b", user_visible=True)
        s = _state(responses=(_answer(),))
        out = await surface_recovery(s)
        assert out.responses == s.responses
    finally:
        rc.reset(token)
