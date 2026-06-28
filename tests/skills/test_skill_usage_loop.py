"""LS7 — closed skill-usage loop (registered → MEASURED).

Asserts on the store's stats (n_executions / success_rate), never on prose:

* (a) ``skill_view`` loads a skill -> n_executions += 1 (the application seam).
* (b) tripwire: a skill is available/"injected" but ``skill_view`` is NEVER
      called -> n_executions does NOT move (the fake-learning guard — counting at
      injection would fail this).
* (c) a verified-success turn that applied a skill nudges success_rate UP; a
      failed turn nudges it DOWN (EWMA from the MEASURED turn outcome).
* (d) a stats-write failure must NOT crash the turn (fail-open).
* (e) gate revival: after enough executions + a low success_rate, the synthesizer's
      deprecate/refine gate predicate now SEES the data it was starved of.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.pipeline.backends.asyncio_backend import _update_skill_success_rates
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest, SkillSource
from stackowl.skills.store import SkillIndexStore
from stackowl.skills.synthesizer import (
    _DEPRECATE_BELOW,
    _MIN_EXECUTIONS_FOR_RATE,
    _REFINE_RANGE,
)
from stackowl.tools.knowledge.skill_view import SkillViewTool


async def _seed(store: SkillIndexStore, name: str, source: SkillSource = "learned") -> int:
    return await store.upsert(
        LoadedSkill(
            manifest=SkillManifest(name=name, description="d", source=source),
            path=Path("/tmp/x"), body="## Steps\n\n1. do it.\n",
            tools_registered=0, owls_registered=0, tool_names=(),
        )
    )


def _state(*viewed_names: str) -> PipelineState:
    return PipelineState(
        trace_id="t-1", session_id="s-1", input_text="hi",
        channel="cli", owl_name="secretary", pipeline_step="",
        tool_calls=tuple(
            ToolCall(
                tool_name="skill_view", args={"name": n},
                result="ok", error=None, duration_ms=1.0,
            )
            for n in viewed_names
        ),
    )


# --------------------------------------------------------- (a) application seam


async def test_skill_view_increments_n_executions(tmp_db) -> None:
    store = SkillIndexStore(tmp_db)
    sid = await _seed(store, "alpha", source="user")
    token = set_services(StepServices(skill_store=store, db_pool=tmp_db))
    try:
        res = await SkillViewTool().execute(name="user:alpha")
        assert res.success, res.error
    finally:
        reset_services(token)
    sk = await store.get("user", "alpha")
    assert sk is not None and sk.skill_id == sid
    assert sk.n_executions == 1


# --------------------------------------------- (b) the fake-learning tripwire


async def test_injected_but_unviewed_skill_does_not_tick(tmp_db) -> None:
    """A skill exists + is enabled (would be INJECTED into the prompt) but
    skill_view is never called -> n_executions must stay 0. If counting happened
    at injection this assertion fails — that is the whole point of the test."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, "ghost", source="learned")
    # A whole turn completes (success), but NO skill_view tool call occurred.
    services = StepServices(skill_store=store)
    await _update_skill_success_rates(services, _state(), success=True)
    sk = await store.get("learned", "ghost")
    assert sk is not None
    assert sk.n_executions == 0          # never applied -> never counted
    assert sk.success_rate is None       # and no measured outcome attributed


# ----------------------------------- (c) success_rate from the MEASURED outcome


async def test_verified_success_nudges_rate_up_failure_down(tmp_db) -> None:
    store = SkillIndexStore(tmp_db)
    await _seed(store, "beta", source="learned")
    services = StepServices(skill_store=store)

    # First applied turn verified-success: seeds at 1.0.
    await _update_skill_success_rates(services, _state("learned:beta"), success=True)
    sk = await store.get("learned", "beta")
    assert sk is not None and sk.success_rate == pytest.approx(1.0)

    # A failed applied turn nudges DOWN (EWMA toward 0).
    await _update_skill_success_rates(services, _state("beta"), success=False)
    sk = await store.get("learned", "beta")
    assert sk is not None and sk.success_rate is not None
    assert sk.success_rate < 1.0

    # A verified-success turn nudges back UP from the lowered rate.
    low = sk.success_rate
    await _update_skill_success_rates(services, _state("beta"), success=True)
    sk = await store.get("learned", "beta")
    assert sk is not None and sk.success_rate is not None
    assert sk.success_rate > low


# ----------------------------------------------------- (d) fail-open guarantee


async def test_stats_write_failure_does_not_crash_turn(tmp_db, monkeypatch) -> None:
    store = SkillIndexStore(tmp_db)
    await _seed(store, "boom", source="learned")

    async def _explode(*_a: object, **_k: object) -> None:
        raise RuntimeError("db is down")

    monkeypatch.setattr(store, "set_success_rate", _explode)
    services = StepServices(skill_store=store)
    # Must return normally (swallowed + logged), never raise.
    await _update_skill_success_rates(services, _state("boom"), success=True)


async def test_skill_view_increment_failure_still_serves(tmp_db, monkeypatch) -> None:
    store = SkillIndexStore(tmp_db)
    await _seed(store, "served", source="user")

    async def _explode(*_a: object, **_k: object) -> None:
        raise RuntimeError("db is down")

    monkeypatch.setattr(store, "increment_n_executions", _explode)
    token = set_services(StepServices(skill_store=store, db_pool=tmp_db))
    try:
        res = await SkillViewTool().execute(name="user:served")
        assert res.success, res.error  # view served despite stats failure
    finally:
        reset_services(token)


# ------------------------------------------------ (e) synthesizer gate revival


async def test_data_now_satisfies_deprecate_gate(tmp_db) -> None:
    """Drive the EXACT inputs the synthesizer's deprecate phase reads
    (success_rate < 0.4 AND n_executions >= 5) and assert its gate predicate now
    selects the skill — proving the loop's inputs are written (the gates were dead
    only because nothing fed them)."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, "loser", source="learned")
    services = StepServices(skill_store=store)
    token = set_services(services)
    try:
        # Apply + fail the turn 6 times: skill_view bumps n_executions, the failed
        # turn outcome drives success_rate down toward 0.
        for _ in range(_MIN_EXECUTIONS_FOR_RATE + 1):
            await SkillViewTool().execute(name="learned:loser")
            await _update_skill_success_rates(
                services, _state("learned:loser"), success=False,
            )
    finally:
        reset_services(token)

    learned = await store.list_for_source("learned")
    # The synthesizer.deprecate_low_performers predicate, replicated:
    candidates = [
        s for s in learned
        if (
            s.enabled
            and s.success_rate is not None
            and s.success_rate < _DEPRECATE_BELOW
            and s.n_executions >= _MIN_EXECUTIONS_FOR_RATE
        )
    ]
    assert [s.name for s in candidates] == ["loser"]
    # And it is NOT mistakenly in the refine band.
    refine = [
        s for s in learned
        if (
            s.success_rate is not None
            and _REFINE_RANGE[0] <= s.success_rate <= _REFINE_RANGE[1]
            and s.n_executions >= _MIN_EXECUTIONS_FOR_RATE
        )
    ]
    assert refine == []
