"""Final-review Finding 1 — `--report` is wired end-to-end: flag allowlist
(owls_helpers.parse_owl_build_flags) -> tool schema (owl_build.parameters) ->
validator (validate_owl_build_spec) -> forge (build_agent_manifest), reachable
from the REAL `/owl create` dispatch path, not a hand-built OwlAgentManifest.

The regression this closes: `test_report_usecase_projects_the_real_handler`
(tests/journeys/commands/test_agent_usecases_as_owls.py) proved the reconcile
projection understands a ReportTrigger, but it builds the manifest BY HAND —
it never goes through the flag parser or the spec validator, so it could not
catch either of them rejecting `--report`. This test drives the genuine
`OwlCommand.handle("create --report ...")` -> `parse_owl_build_flags` ->
`OwlBuildTool.execute` -> `validate_owl_build_spec` -> `build_agent_manifest`
chain end to end (mirrors the harness in test_owl_build_schedule.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.owls_command import OwlCommand
from stackowl.commands.owls_helpers import parse_owl_build_flags
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.trigger import ReportTrigger
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.scheduler.owl_lifecycle import _job_id_for
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
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


class _State:
    session_id = "s1"
    trace_id = "t1"
    channel = "cli"
    reply_target = None


def test_parse_owl_build_flags_accepts_report() -> None:
    """The flag allowlist maps --report to the `report` kwarg (Finding 1.1)."""
    kwargs = parse_owl_build_flags(
        '--name BriefOwl --report morning_brief --schedule "daily@08:00"'
    )
    assert kwargs == {
        "name": "BriefOwl", "report": "morning_brief", "schedule": "daily@08:00",
    }


async def test_owl_create_report_flag_end_to_end_no_capability_or_specialty(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """`/owl create --name BriefOwl --report morning_brief --schedule ...` mints a
    scheduled owl carrying a REAL ReportTrigger, through the genuine dispatcher +
    flag parser + validator + forge — with NO preset/explicit_tools/specialty
    supplied (Finding 1.2/1.3: the tool schema advertises `report`, and the
    validator no longer demands capability/specialty when `report` is set)."""
    registry = OwlRegistry.with_default_secretary()
    token = set_services(_services(tmp_db, registry))
    trace = TraceContext.start(
        session_id="s1", trace_id="t1", interactive=True, channel="cli",
        delegation_depth=0, owl_name="secretary",
    )
    try:
        out = await OwlCommand().handle(
            'create --name BriefOwl --report morning_brief --schedule "daily@08:00"',
            _State(),
        )
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert not out.startswith("✗"), out  # not the "✗" refusal prefix

    manifest = registry.get("BriefOwl")
    assert manifest.lifecycle == "scheduled"
    assert isinstance(manifest.trigger, ReportTrigger)
    assert manifest.trigger.report == "morning_brief"
    assert manifest.trigger.schedule == "daily@08:00"

    # The reconcile loop (wired on create) projected exactly one owned job row
    # against the REAL morning_brief handler, not the generic goal_execution path.
    jobs = await JobScheduler(db=tmp_db).list_jobs()
    job = next((j for j in jobs if j.job_id == _job_id_for("BriefOwl")), None)
    assert job is not None, "no projected job row for the report-pinned owl"
    assert job.handler_name == "morning_brief"
