"""TS9 — trustworthy confirmation: prove a scheduled owl, don't just claim it.

A SCHEDULED create's success message must PROVE the schedule with the REAL next fire
time read from the projected job row (``next_run_at`` of ``owl_lifecycle-<name>``) — a
concrete, user-verifiable instant — plus an honest "it reaches you proactively on its
own" line and a one-line off-ramp ("say 'stop <name>'"). An on-demand create's message
stays byte-identical. We assert the timestamp in the message is exactly the job row's.

Drives the GENUINE :class:`OwlBuildTool` against a REAL registry + yaml + db (only the
AI provider is faked — owl_build never consults it for a fully-specified create).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.scheduler.owl_lifecycle import _job_id_for
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


class _ScriptedProvider:
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


async def test_a_scheduled_confirmation_proves_with_real_next_run(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """(a) the scheduled-create message carries the REAL next_run_at from the job row
    (not a fabricated time) + a one-line pause off-ramp."""
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
    # The timestamp in the message is EXACTLY the projected job row's next_run_at —
    # measured from the world, never invented.
    jobs = await JobScheduler(db=tmp_db).list_jobs()
    row = next(j for j in jobs if j.job_id == _job_id_for("brain"))
    assert row.next_run_at in result.output, (
        "confirmation must quote the job row's real next_run_at, got: " + result.output
    )
    # Honest proactivity + the off-ramp hint are present.
    low = result.output.lower()
    assert "proactive" in low
    assert "stop brain" in low  # the pause off-ramp


async def test_b_on_demand_confirmation_is_unchanged(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """(b) an on-demand create's message stays byte-identical (no schedule prose)."""
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
    assert result.output.startswith("Created owl 'scout'")
    assert "Delegate to it with delegate_task." in result.output
    assert "Next run" not in result.output  # no schedule confirmation for on_demand
