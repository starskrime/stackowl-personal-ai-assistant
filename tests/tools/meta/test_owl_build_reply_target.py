"""Story 3.5 — owl_build's create-consent call threads ``reply_target`` off
``TraceContext`` into ``gate.policy.request(...)`` — the exact call site named
in the 2026-08-19 incident (``ConsentRequest.reply_target``'s own docstring).

``@pytest.mark.no_official_origin``: this test is ABOUT consent (it needs the
request to actually reach the prompter), so it opts out of conftest's
autouse ``cli`` channel registration — see ``tests/tools/meta/conftest.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, ConsentScope
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


class _SpyPrompter:
    def __init__(self, scope: ConsentScope = ConsentScope.DENY) -> None:
        self.seen: list[object] = []
        self._scope = scope

    async def prompt(self, req: object) -> ConsentScope:
        self.seen.append(req)
        return self._scope


def _services(tmp_db: DbPool, registry: OwlRegistry, gate: ConsequentialActionGate) -> StepServices:
    return StepServices(
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=registry,
        consent_gate=gate,
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        db_pool=tmp_db,
    )


@pytest.mark.no_official_origin
async def test_owl_build_create_consent_carries_reply_target(tmp_home: Path, tmp_db: DbPool) -> None:
    spy = _SpyPrompter(ConsentScope.DENY)
    registry = OwlRegistry.with_default_secretary()
    gate = ConsequentialActionGate(ConsentPolicy(prompter=spy))  # type: ignore[arg-type]
    services = _services(tmp_db, registry, gate)

    svc_token = set_services(services)
    trace_token = TraceContext.start(
        session_key="s-owl", trace_id="t-owl", interactive=True, channel="telegram",
        delegation_depth=0, owl_name="secretary", reply_target=72055773,
    )
    try:
        await OwlBuildTool().execute(
            action="create", name="scout", preset="researcher", specialty="recon",
        )
    finally:
        TraceContext.reset(trace_token)
        reset_services(svc_token)

    assert spy.seen and spy.seen[0].reply_target == 72055773
