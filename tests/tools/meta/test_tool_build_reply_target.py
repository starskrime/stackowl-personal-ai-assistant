"""Story 3.5 — tool_build's create-consent call threads ``reply_target`` off
``TraceContext`` into ``gate.policy.request(...)``, so the socket-hardened
refusal in ``GatewayLink._handle_consent`` (AC3) never regresses this live,
human-attended delivery path.

Forces the prompt (rather than the reversible auto-proceed) via the
always-ask exclusion list, mirroring ``test_tool_build_reversible_consent.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, ConsentScope
from stackowl.tools.meta.tool_build import ToolBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


class _SpyPrompter:
    def __init__(self, scope: ConsentScope = ConsentScope.DENY) -> None:
        self.seen: list[object] = []
        self._scope = scope

    async def prompt(self, req: object) -> ConsentScope:
        self.seen.append(req)
        return self._scope


def _services(tmp_db: DbPool, gate: ConsequentialActionGate) -> StepServices:
    return StepServices(
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=gate,
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        db_pool=tmp_db,
    )


@pytest.mark.no_official_origin
async def test_tool_build_create_consent_carries_reply_target(tmp_home: Path, tmp_db: DbPool) -> None:
    spy = _SpyPrompter(ConsentScope.DENY)
    gate = ConsequentialActionGate(
        ConsentPolicy(
            prompter=spy,  # type: ignore[arg-type]
            always_ask_tools=frozenset({"tool_build"}),
        )
    )
    services = _services(tmp_db, gate)

    svc_token = set_services(services)
    trace_token = TraceContext.start(
        session_key="s-tb", trace_id="t-tb", interactive=True, channel="telegram",
        owl_name="secretary", reply_target=72055773,
    )
    try:
        await ToolBuildTool().execute(
            action="create",
            name="reply_target_probe",
            description="echo a string verbatim via printf",
            params=[
                {"name": "text", "type": "string", "description": "the text", "required": True}
            ],
            argv_template=["printf", "%s", "{text}"],
            action_severity="read",
        )
    finally:
        TraceContext.reset(trace_token)
        reset_services(svc_token)

    assert spy.seen and spy.seen[0].reply_target == 72055773
