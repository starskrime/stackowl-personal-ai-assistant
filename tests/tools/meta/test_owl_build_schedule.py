"""TS8 — schedule-as-slot in owl_build + TS7 — disjoint owl/skill descriptions.

A ``create`` that names a recurring cadence mints a ``lifecycle='scheduled'`` owl
with a :class:`CronTrigger`, and the UniOwl reconcile loop (already wired on create)
auto-provisions its recurring job row. A too-fast cadence is refused with a clear
interval-floor error (no crash, no owl). An on-demand create (no schedule) stays
byte-identical. The owl_build vs skill_manage descriptions are disjoint along
who(owl) / how(skill).

Drives the GENUINE :class:`OwlBuildTool` against a REAL registry + yaml + db (only
the AI provider is faked — owl_build never consults it for a fully-specified create).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import CronTrigger
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.scheduler.owl_lifecycle import _job_id_for
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.knowledge.skill_manage import SkillManageTool
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


class _ScriptedProvider:
    """Registry-shaped provider stand-in — owl_build never consults the model."""

    protocol = "anthropic"

    async def complete_with_tools(self, *a, **k):  # pragma: no cover
        return ("", [])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""

    def get(self, name: str) -> _ScriptedProvider:
        return self

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
        return self


def _services(tmp_db: DbPool, registry: OwlRegistry) -> StepServices:
    return StepServices(
        provider_registry=_ScriptedProvider(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=registry,
        consent_gate=ConsequentialActionGate(
            ConsentPolicy(tiers={"owl_build": TrustTier.AUTO})
        ),
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        db_pool=tmp_db,
    )


def _trace() -> object:
    return TraceContext.start(
        session_id="s", trace_id="t", interactive=True, channel="cli",
        delegation_depth=0, owl_name="secretary",
    )


async def test_a_scheduled_create_mints_cron_trigger_and_job_row(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """(a) create with schedule 'every 2h' → a lifecycle='scheduled' owl carrying a
    CronTrigger, AND (via reconcile) its projected job row exists → verified=True."""
    registry = OwlRegistry.with_default_secretary()
    token = set_services(_services(tmp_db, registry))
    trace = _trace()
    try:
        result = await OwlBuildTool()(
            action="create", name="brain", preset="researcher",
            specialty="poke me with AI news", schedule="every 2h",
            goal="find the latest AI news and send me 1-3 items",
        )
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert result.success, result.error
    assert result.verified is True, "a real scheduled create must MEASURE verified=True"

    manifest = registry.get("brain")
    assert manifest.lifecycle == "scheduled"
    assert isinstance(manifest.trigger, CronTrigger)
    assert manifest.trigger.schedule == "every 2h"
    assert manifest.trigger.prompt == "find the latest AI news and send me 1-3 items"

    # The reconcile loop (wired on create) projected exactly one owned job row.
    jobs = await JobScheduler(db=tmp_db).list_jobs()
    assert any(j.job_id == _job_id_for("brain") for j in jobs), "no projected job row"


async def test_b_too_fast_schedule_is_refused_with_floor_error_no_owl(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """(b) a sub-floor cadence 'every 1m' → a clear interval-floor error, no crash,
    and NO owl minted (refused before forge/consent)."""
    registry = OwlRegistry.with_default_secretary()
    token = set_services(_services(tmp_db, registry))
    trace = _trace()
    try:
        result = await OwlBuildTool()(
            action="create", name="speedy", preset="researcher",
            specialty="too fast", schedule="every 1m",
        )
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert result.success is False
    assert "5 minutes" in result.error, result.error
    # No owl was created (refused before forge/consent).
    assert not any(m.name == "speedy" for m in registry.all())


async def test_c_on_demand_create_unchanged_no_trigger(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """(c) a create with NO schedule is byte-identical: lifecycle='on_demand',
    no trigger, and no projected job row."""
    registry = OwlRegistry.with_default_secretary()
    token = set_services(_services(tmp_db, registry))
    trace = _trace()
    try:
        result = await OwlBuildTool()(
            action="create", name="scout", preset="researcher", specialty="recon",
        )
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert result.success, result.error
    assert result.verified is True
    manifest = registry.get("scout")
    assert manifest.lifecycle == "on_demand"
    assert manifest.trigger is None
    jobs = await JobScheduler(db=tmp_db).list_jobs()
    assert not any(j.job_id == _job_id_for("scout") for j in jobs)


def test_d_owl_build_and_skill_manage_descriptions_are_disjoint() -> None:
    """(d) the two creation tools the model picks between describe disjoint mental
    models: owl=a named scheduled/proactive agent; skill=a procedure, not an agent."""
    owl_desc = OwlBuildTool().manifest.description.lower()
    skill_desc = SkillManageTool().manifest.description.lower()

    # owl_build is the AGENT/persona that schedules + reaches the user proactively.
    assert "agent" in owl_desc
    assert "persona" in owl_desc
    assert "schedule" in owl_desc
    assert "proactive" in owl_desc

    # skill_manage is a PROCEDURE; it must NOT advertise itself as a standalone agent.
    assert "procedure" in skill_desc
    assert "not a standalone agent" in skill_desc
    assert "never messages the user" in skill_desc
    assert "never runs on a schedule" in skill_desc


def test_e_stale_not_implemented_docstring_is_gone() -> None:
    """(e) the misleading 'create/edit/retire raise NotImplementedError' docstring
    (they are fully implemented) is removed from the owl_build module."""
    import stackowl.tools.meta.owl_build as owl_build_module

    assert "NotImplementedError" not in (owl_build_module.__doc__ or "")
