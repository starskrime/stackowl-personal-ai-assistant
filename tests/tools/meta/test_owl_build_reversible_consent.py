"""Task 8 — owl_build routes create-consent through the ONE ConsequentialActionGate
with ``reversible=True``, whose HONEST undo handle is ``action='retire'``.

``retire`` is a genuine deactivation, not a flag flip: it deregisters the owl (so it
can no longer be reached / delegated to / act), removes its durable yaml entry (no
boot resurrection), and — via the reconcile loop — deletes its owned scheduler job
row (so a scheduled/proactive owl stops firing). That real undo is what makes the
create's auto-proceed honest rather than nominal.

Tests:
  * a reversible create AUTO-PROCEEDS (prompt skipped, audit exercised);
  * an always-ask owl_build still PROMPTS (fail-closed preserved);
  * retire genuinely deactivates a SCHEDULED owl (unreachable + job row gone).

Only the consent PROMPTER is a spy; the registry, yaml, db, reconcile + retire are
all REAL, so a removed wiring fails the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.trace import TraceContext
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.scheduler.owl_lifecycle import _job_id_for
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, ConsentScope
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


class _SpyPrompter:
    def __init__(self, scope: ConsentScope = ConsentScope.DENY) -> None:
        self.calls = 0
        self._scope = scope

    async def prompt(self, req: object) -> ConsentScope:
        self.calls += 1
        return self._scope


class _AuditSpy:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, event: str, *, actor: str, target: str, details: dict) -> None:
        self.rows.append(details)


def _services(tmp_db: DbPool, registry: OwlRegistry, gate: ConsequentialActionGate) -> StepServices:
    return StepServices(
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=registry,
        consent_gate=gate,
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        db_pool=tmp_db,
    )


async def _run(services: StepServices, args: dict) -> object:
    svc_token = set_services(services)
    trace_token = TraceContext.start(
        session_id="s-owl", trace_id="t-owl", interactive=True, channel="cli",
        delegation_depth=0, owl_name="secretary",
    )
    try:
        return await OwlBuildTool().execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(svc_token)


async def test_reversible_create_auto_proceeds_without_prompt(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """A reversible owl_build create skips the PROMPT but exercises the AUDIT."""
    spy = _SpyPrompter()
    audit = _AuditSpy()
    registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(
        ConsentPolicy(prompter=spy, audit_logger=audit)  # type: ignore[arg-type]
    )
    services = _services(tmp_db, registry, gate)

    result = await _run(
        services,
        {"action": "create", "name": "scout", "preset": "researcher", "specialty": "recon"},
    )

    assert result.success, result.error
    assert spy.calls == 0, "reversible create must not bother the user with a prompt"
    assert any(d.get("reason") == "reversible_auto" for d in audit.rows), audit.rows
    assert registry.get("scout").origin == "agent"


async def test_always_ask_owl_build_still_prompts(tmp_home: Path, tmp_db: DbPool) -> None:
    """owl_build on the always-ask list still prompts; a DENY blocks the mint."""
    spy = _SpyPrompter(ConsentScope.DENY)
    registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(
        ConsentPolicy(
            prompter=spy,  # type: ignore[arg-type]
            always_ask_tools=frozenset({"owl_build"}),
        )
    )
    services = _services(tmp_db, registry, gate)

    result = await _run(
        services,
        {"action": "create", "name": "scout", "preset": "researcher", "specialty": "recon"},
    )

    assert not result.success
    assert spy.calls == 1, "an always-ask owl_build must still be prompted"
    with pytest.raises(OwlNotFoundError):
        registry.get("scout")


async def test_retire_genuinely_deactivates_scheduled_owl(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """retire is a REAL undo: the scheduled owl becomes unreachable (can no longer be
    resolved/act), leaves no durable record, and its projected job row is deleted."""
    spy = _SpyPrompter()  # never consulted — create auto-proceeds (reversible)
    registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(ConsentPolicy(prompter=spy))  # type: ignore[arg-type]
    services = _services(tmp_db, registry, gate)

    # Mint a SCHEDULED, proactive owl and prove it is live + has its owned job row.
    created = await _run(
        services,
        {
            "action": "create", "name": "brain", "preset": "researcher",
            "specialty": "poke me with AI news", "schedule": "every 2h",
            "goal": "find the latest AI news",
        },
    )
    assert created.success, created.error
    assert registry.get("brain").lifecycle == "scheduled"  # reachable + acting
    jobs = await JobScheduler(db=tmp_db).list_jobs()
    assert any(j.job_id == _job_id_for("brain") for j in jobs), "no projected job row pre-retire"

    # UNDO — retire the owl.
    retired = await _run(services, {"action": "retire", "name": "brain"})
    assert retired.success, retired.error

    # 1. Cannot act / be reached — the routing + delegation entry point now refuses.
    with pytest.raises(OwlNotFoundError):
        registry.get("brain")
    # 2. No durable resurrection — the yaml no longer carries it.
    assert not OwlBuildTool._yaml_has_owl("brain"), "retired owl still in durable yaml"
    # 3. Stops firing proactively — its owned scheduler row was deleted by reconcile.
    jobs_after = await JobScheduler(db=tmp_db).list_jobs()
    assert not any(j.job_id == _job_id_for("brain") for j in jobs_after), (
        "retired scheduled owl still owns a live job row (would keep firing)"
    )
