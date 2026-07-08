"""Unknown-tool dispatch must be a REAL, ledger'd failure — not a silent
non-failed string — so the existing structural give-up / circuit-breaker /
persistence-nudge machinery can see it and steer the model toward building
the missing capability instead of looping or giving up silently.
"""
from __future__ import annotations

import pytest
from tests.pipeline.test_dispatch_substitution import _build_real_dispatch, _FakeRegistry

from stackowl.infra import tool_outcome_ledger
from stackowl.pipeline.persistence import TOOL_FAILED_MARKER


@pytest.mark.asyncio
async def test_unknown_tool_dispatch_carries_failed_marker(monkeypatch):
    """The dispatcher's honest failure marker must be present so the provider
    layer marks this call ``failed=True`` (closing the LLM-judge blind spot:
    without the marker, ``summarize_tool_outcomes`` renders this as ``(ok)``)."""
    from stackowl.pipeline.steps import execute as exe

    reg = _FakeRegistry([])
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    out = await dispatch("nonexistent_tool_xyz", {})

    assert out.startswith(TOOL_FAILED_MARKER)


@pytest.mark.asyncio
async def test_unknown_tool_dispatch_records_ledger_outcome(monkeypatch):
    """The unknown-tool call must land in the turn-scoped ledger as a
    non-effectful failure (side_effect_committed=False — nothing ran) so it is
    visible for observability without being misread as a failed consequential
    effect."""
    from stackowl.pipeline.steps import execute as exe

    reg = _FakeRegistry([])
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    token = tool_outcome_ledger.bind()
    try:
        await dispatch("nonexistent_tool_xyz", {})
        outcomes = tool_outcome_ledger.get_outcomes()
    finally:
        tool_outcome_ledger.reset(token)

    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.name == "nonexistent_tool_xyz"
    assert o.success is False
    assert o.side_effect_committed is False
    # Not effectful (side_effect_committed=False) — must not falsely trip the
    # consequential-give-up path meant for real write/consequential attempts.
    cf, cs = tool_outcome_ledger.consequential_tally()
    assert cf == 0 and cs == 0


@pytest.mark.asyncio
async def test_unknown_tool_repeat_trips_circuit_breaker(monkeypatch):
    """Calling the SAME nonexistent tool repeatedly must trip the existing
    TurnProgressTracker circuit breaker (same containment as any other
    repeatedly-failing tool) instead of looping unbounded."""
    from stackowl.pipeline.steps import execute as exe

    reg = _FakeRegistry([])
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg)

    for _ in range(4):
        await dispatch("nonexistent_tool_xyz", {})

    # By the 5th call the circuit-open bounce (a different, shorter refusal
    # string) has replaced the raw "not found" marker path — structural proof
    # the tracker's own state opened, not just "it kept failing".
    out5 = await dispatch("nonexistent_tool_xyz", {})
    assert "not found" not in out5.lower()
